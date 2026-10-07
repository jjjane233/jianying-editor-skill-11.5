"""剪映 Infinite Canvas native bridge 客户端 (11.5.0.14471 实测可用).

剪映 11.5 的 VECreator.dll 里内建了一个本地 HTTP 服务 (native bridge),
Web 前端 (Resources/canvas_agent/app) 与原生层之间的所有"剪辑动作"都走它.
拿到 discovery 文件里的 port + token 之后, 外部程序可以直接调用同一批接口,
等价于"让 AI 直接操作剪映".

发现机制 (逆向自 VECreator.dll @0xc6ce6xx)
------------------------------------------
  剪映启动时把 {app,pid,port,token,version} 写到:
      %TEMP%\\infinite-canvas-native-bridge.json
  实测内容:
      {"app":"infinite_canvas","pid":23156,"port":54549,
       "token":"75d24932-3b4f-45e6-84c9-e66c88deddad","version":1}

鉴权 (逆向自 app-server.mjs, 变量 Ya0)
--------------------------------------
      请求头:  x-infinite-canvas-native-token: <token>
      健康检查: GET /.infinite-canvas-native-bridge/health     <- 注意前导 "/." 
      版本接口: GET /infinite-canvas/version?token=<token>
      (早前用 /infinite-canvas-native-bridge/health 会 401, 因为少了前导点)

路由
----
      GET    /.infinite-canvas-native-bridge/health      探活
      GET    /infinite-canvas/drafts                     列出本会话草稿
      POST   /infinite-canvas/session                    建会话
      DELETE /infinite-canvas/session/<id>               关会话
      POST   /infinite-canvas/session/<id>/jsb           JSB 方法调用 (核心)
      POST   /custom-agent/media-tool-event              素材分析完成事件
      POST   /custom-agent/context                       注入上下文

本模块只依赖标准库.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import time
from typing import Any, Dict, List, Optional, Tuple

DISCOVERY_NAME = "infinite-canvas-native-bridge.json"
TOKEN_HEADER = "x-infinite-canvas-native-token"
HEALTH_PATH = "/.infinite-canvas-native-bridge/health"
DEFAULT_TIMEOUT = 15.0


class BridgeUnavailable(RuntimeError):
    """剪映没在跑, 或 native bridge 没起来."""


class BridgeError(RuntimeError):
    """bridge 返回了业务错误."""

    def __init__(self, status: int, payload: Any):
        self.status = status
        self.payload = payload
        msg = payload.get("message") if isinstance(payload, dict) else str(payload)
        super().__init__("bridge HTTP %s: %s" % (status, (msg or "")[:200]))


# --------------------------------------------------------------------------
# 发现
# --------------------------------------------------------------------------
def _temp_dirs(extra: Optional[List[str]] = None) -> List[str]:
    out: List[str] = []
    for d in (
        os.environ.get("TEMP"),
        os.environ.get("TMP"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Temp"),
    ):
        if d and d not in out and os.path.isdir(d):
            out.append(d)
    for d in extra or []:
        if d and d not in out:
            out.append(d)
    return out


def _pid_alive(pid: int) -> bool:
    if not pid:
        return True
    try:
        k32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if h:
            k32.CloseHandle(h)
            return True
        return False
    except Exception:
        return True


def discover(extra_dirs: Optional[List[str]] = None, require_alive: bool = True) -> Optional[Dict[str, Any]]:
    """扫描 discovery 文件, 返回 {app,pid,port,token,version,discovery_path}."""
    for d in _temp_dirs(extra_dirs):
        p = os.path.join(d, DISCOVERY_NAME)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            continue
        if not j.get("port") or not j.get("token"):
            continue
        if require_alive and not _pid_alive(j.get("pid")):
            continue
        j["discovery_path"] = p
        return j
    return None


# --------------------------------------------------------------------------
# 客户端
# --------------------------------------------------------------------------
class Bridge:
    """Infinite Canvas native bridge 的同步客户端."""

    def __init__(self, port: Optional[int] = None, token: Optional[str] = None,
                 timeout: float = DEFAULT_TIMEOUT, ctx: Optional[Dict[str, Any]] = None):
        self.timeout = timeout
        self.info: Dict[str, Any] = dict(ctx or {})
        if port and token:
            self.info.update({"port": port, "token": token})
        else:
            found = discover()
            if not found:
                raise BridgeUnavailable(
                    "没找到 %s, 请确认剪映 11.x 正在运行" % DISCOVERY_NAME)
            self.info.update(found)
        self.port = int(self.info["port"])
        self.token = str(self.info["token"])

    # ---- 底层 ----
    def request(self, method: str, path: str, body: Any = None,
                headers: Optional[Dict[str, str]] = None,
                timeout: Optional[float] = None) -> Tuple[int, Any]:
        payload = b""
        h = {
            "Host": "127.0.0.1:%d" % self.port,
            "Connection": "close",
            TOKEN_HEADER: self.token,
        }
        if headers:
            h.update(headers)
        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            h["Content-Type"] = "application/json; charset=utf-8"
            h["Content-Length"] = str(len(payload))
        req = ("%s %s HTTP/1.1\r\n" % (method, path)
               + "".join("%s: %s\r\n" % kv for kv in h.items()) + "\r\n")
        s = socket.create_connection(("127.0.0.1", self.port), timeout=timeout or self.timeout)
        try:
            s.sendall(req.encode("utf-8") + payload)
            s.settimeout(timeout or self.timeout)
            buf = b""
            while len(buf) < 8_000_000:
                try:
                    c = s.recv(65536)
                except socket.timeout:
                    break
                if not c:
                    break
                buf += c
        finally:
            s.close()
        head, _, data = buf.partition(b"\r\n\r\n")
        lines = head.decode("latin-1").split("\r\n")
        status = 0
        if lines and lines[0].startswith("HTTP/"):
            try:
                status = int(lines[0].split()[1])
            except (IndexError, ValueError):
                status = 0
        try:
            parsed: Any = json.loads(data.decode("utf-8"))
        except Exception:
            parsed = data.decode("utf-8", "replace")
        return status, parsed

    def __call__(self, method: str, path: str, body: Any = None, **kw) -> Any:
        status, payload = self.request(method, path, body, **kw)
        if status != 200:
            raise BridgeError(status, payload)
        return payload

    # ---- 探活 ----
    def health(self) -> Dict[str, Any]:
        return self.request("GET", HEALTH_PATH)[1]

    def version(self) -> Dict[str, Any]:
        return self.request("GET", "/infinite-canvas/version?token=%s" % self.token)[1]

    def alive(self) -> bool:
        try:
            return bool(self.health().get("ok"))
        except Exception:
            return False

    # ---- 会话 ----
    def open_session(self, **params) -> str:
        r = self("POST", "/infinite-canvas/session", params or {})
        sid = r.get("session_id")
        if not sid:
            raise BridgeError(200, r)
        return sid

    def close_session(self, sid: str) -> Any:
        return self.request("DELETE", "/infinite-canvas/session/%s" % sid)[1]

    def drafts(self) -> Any:
        return self("GET", "/infinite-canvas/drafts")

    # ---- JSB ----
    def jsb_raw(self, sid: str, method: str, params: Any = None,
                timeout: Optional[float] = None) -> Dict[str, Any]:
        """原样返回 {"code":..,"msg":..,"data":..}; code 含义:
        1=成功 0=失败 -2=方法未注册 -3=参数非法
        """
        return self("POST", "/infinite-canvas/session/%s/jsb" % sid,
                    {"method": method, "params": params if params is not None else {}},
                    timeout=timeout)

    def jsb(self, sid: str, method: str, params: Any = None,
            timeout: Optional[float] = None) -> Any:
        r = self.jsb_raw(sid, method, params, timeout)
        if r.get("code") != 1:
            raise BridgeError(r.get("code") or 0, r)
        return r.get("data")

    # ---- 常用封装 ----
    def create_draft(self, session: Optional[str] = None, **params) -> Dict[str, Any]:
        sid = session or self.open_session()
        return self.jsb(sid, "createDraft", params)

    def agent_server_context(self, session: Optional[str] = None) -> Dict[str, Any]:
        sid = session or self.open_session()
        return self.jsb(sid, "getLocalAgentServerContext", {})

    def debug_context(self, session: Optional[str] = None) -> Dict[str, Any]:
        sid = session or self.open_session()
        return self.jsb(sid, "getDebugContext", {})


# ---- 已实测注册的 JSB 方法 (11.5.0.14471) ----
# code=1 成功  |  名字取自 VECreator.dll @0xc6c5100 的注册表, 状态为本机实测
JSB_METHODS: Dict[str, str] = {
    "createDraft": "新建 Infinite Canvas 画布草稿, 返回 draft_id/draft_path",
    "getLocalAgentServerContext": "取本地 agent server 的 port + localServerToken",
    "getDebugContext": "取 canvas_agent 包路径/manifest",
    "getAppInfo": "取 app 版本/主题等信息",
    "getWebviewPerfTime": "取 webview 性能数据",
    "decryptContent": "调原生解密",
    "createDraftStore": "建草稿存储",
    "notifyDraftSaved": "通知草稿已保存",
    "notifyDraftCoverChanged": "通知封面变化",
    "closeTab": "关闭标签页",
    "openSetting": "打开设置",
    "openProject": "打开工程",
    "openExportWindow": "打开导出窗口",
    "openShortcutPanel": "打开快捷键面板",
    "getRemoveAigcWatermark": "查询是否去 AIGC 水印",
    "setRemoveAigcWatermark": "设置去 AIGC 水印",
    "setTitle": "设标题",
    "enterMultitrack": "进入多轨模式(需参数)",
    "exportDraft": "导出草稿(需参数)",
    "getMaterialListV2": "取素材列表",
    "getDroppedMaterialListV2": "取拖入素材列表",
    "cancelMedia": "取消素材导入",
    "retryUploadMedia": "重试上传",
    "getAudioWaveformData": "取音频波形(需参数)",
    "getToneCategories": "取音色分类",
    "getJimengAccountStatus": "即梦账号绑定状态",
    "openJimengAccountBind": "打开即梦绑定",
    "getCloudAssetSpaces": "云空间列表",
    "transcodeMedia": "转码(需参数)",
    "extractMedia": "提取素材(需参数)",
    "extractMediaCover": "抽封面(需参数)",
    "processMediaAsset": "处理素材(需参数)",
    "uploadLocalMedia": "上传本地素材(需参数)",
    "requestPermissions": "申请权限",
    "getFileInfo": "取文件信息",
    "importMedia": "导入素材(需参数)",
    "downloadOnlineFile": "下载在线文件(需参数)",
}


# 会让剪映弹窗/改状态的方法, 自检时跳过 (调用会阻塞或产生副作用)
SIDE_EFFECT_METHODS = frozenset({
    "openSetting", "openProject", "openExportWindow", "openShortcutPanel",
    "exportDraft", "enterMultitrack", "closeTab", "openJimengAccountBind",
    "setRemoveAigcWatermark", "notifyDraftSaved", "notifyDraftCoverChanged",
    "createDraft", "selectMedia", "importMedia", "increaseDraft",
})


def describe(probe: bool = True, timeout: float = 4.0) -> Dict[str, Any]:
    """自检: 报告 discovery / health / 已注册方法.

    默认 probe=True 时逐个试方法, 通过 code 区分是否注册.
    带副作用的 (弹窗/改状态) 会被跳过, 避免打断剪映使用.
    """
    out: Dict[str, Any] = {"discovery": discover(require_alive=False)}
    b = Bridge(timeout=timeout)
    out["port"] = b.port
    out["token_prefix"] = b.token[:8] + "..."
    out["health"] = b.health()
    sid = b.open_session()
    out["session"] = sid
    out["jsb_methods_documented"] = dict(JSB_METHODS)
    if not probe:
        return out
    reg, unreg, skipped, errored = [], [], [], []
    for m in JSB_METHODS:
        if m in SIDE_EFFECT_METHODS:
            skipped.append(m)
            continue
        try:
            r = b.jsb_raw(sid, m, {}, timeout=timeout)
            code = r.get("code")
            if code == -2:
                unreg.append(m)
            else:
                reg.append((m, code))
        except Exception as e:
            errored.append((m, str(e)[:60]))
    out["registered"] = reg
    out["unregistered"] = unreg
    out["skipped_side_effect"] = skipped
    out["probe_errors"] = errored
    return out


if __name__ == "__main__":
    import io as _io
    import sys as _sys
    _sys.stdout = _io.TextIOWrapper(_sys.stdout.buffer, encoding="utf-8", errors="replace")
    print(json.dumps(describe(), ensure_ascii=False, indent=2))
