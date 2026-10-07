"""剪映官方 CustomAgent 运行时客户端 (11.5.0.14471 实测定型).

与 `appserver.py` 的区别
------------------------
`appserver.py`  **自己拉起**一个隔离的 bun app-server, 适合"无剪映也能跑"的
              场景, 但它**没有 host context** —— 所有需要 tdid/pf/appvr/
               登录 cookie 的官方 broker 能力 (`/openagent/v1/...`) 全部 500。

本模块        **附着到剪映进程自己拉起的那个 app-server**, 复用它的 host
               context 与登录态。实测定型的链路:

    剪映主进程 (JianyingPro.exe)
        └── VECreator.dll  : 进程内 native bridge
              %TEMP%\\infinite-canvas-native-bridge.json  {port, token}
              └── JSB getLocalAgentServerContext
                    → {localServerToken, port=<agent server>}
                        └── bun app-server  (:60274 之类)
                              /api/agent-runtime/v1/...   控制平面
                              /api/custom-agent/...       产品平面
                              → broker → https://lv-pc-api.ulikecam.com/openagent/v1/*
                                 (带 tdid/pf/appvr/sign/cookie, 实测 cookiePresent=true)

鉴权 (实测)
-----------
* native bridge : 头 `x-infinite-canvas-native-token: <bridge token>`
* agent server  : 头 `x-custom-agent-token: <localServerToken>`   (非空即通过)
* 需要 runtime 绑定的路由: 再加
      `x-agent-runtime-instance-id: <instanceId>`
      `x-agent-runtime-session-id:  <sessionId>`
  例如 `/api/custom-agent/host-capabilities/manifest` 缺这两头 → 400
  `runtime_binding_required`; 配上但仍无实例 → 404 `instance_not_found`。

控制平面 (实测可用)
-------------------
    POST /api/agent-runtime/v1/instances:resolve    建/取实例 (201 created)
    GET  /api/agent-runtime/v1/instances/<id>/sessions
    GET  /api/agent-runtime/v1/host/health

运行时指令 (实测可用, 45 种 kind)
    POST /api/custom-agent/commands  {requestId, compact|responseView, command}
      SubmitTask / AdvanceRun / OpenWorkspaceRun / SendUserMessage /
      ConfirmPlan / ResolvePermission / RunStage / CancelRun / ...
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

__all__ = [
    "AgentRuntimeError", "LiveServer", "find_live_server",
    "AgentRuntimeClient", "COMMAND_KINDS", "RUNTIME_MODES",
    "MEDIA_EXTS",
]

# 可注册为素材的扩展名 (local-inputs/register-paths 按此过滤)
MEDIA_EXTS = (
    ".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm",
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg",
    ".txt", ".srt", ".vtt", ".md", ".json",
)

DEFAULT_TIMEOUT = 60.0

# 逆向 app-server.mjs 得到的指令白名单 (xl0 + dispatchCommand)
COMMAND_KINDS = (
    "SubmitTask", "AdvanceRun", "RunNextStep", "OpenWorkspaceRun", "OpenTask",
    "SendUserMessage", "ConfirmPlan", "ConfirmPlanAndContinue",
    "ResolvePermission", "ResolvePermissionAndContinue", "ApprovePermission",
    "RunStage", "CancelRun", "CancelStage", "RecoverRun",
    "DispatchAvailableCommand", "RouteAgentMode", "RegenerateStage",
    "RegenerateRuntimeOutput", "SelectStageHistoryAsset", "DisableRuntimeOutput",
    "ResolveMemoryProposal", "UpsertAgent", "RegisterAgent", "UpdateAgent",
    "DisableAgent", "RemoveAgent", "SetAgentAvailability", "RemoveTask",
    "RenameTask", "ResetUserState", "ResolveProviderAuth",
)

# 官方内置 Agent 画像 (SubmitTask 后从 run snapshot 读取, 实测)
RUNTIME_MODES = {
    "v2-director-agent": "创作统筹 (理解意图, 编排资产/成片/画布/专业 Agent)",
    "v2-script-agent": "文案与粗剪内容选择 (输出 contract: script)",
    "v2-editing-agent": "时间线装配与包装 (输出 contract: edited_draft)",
    "v2-review-agent": "证据驱动审片 (输出 contract: review_report)",
}


class AgentRuntimeError(RuntimeError):
    """官方运行时调用失败."""

    def __init__(self, message: str, status: int = 0, payload: Any = None,
                 code: str = ""):
        super().__init__(message)
        self.status = status
        self.payload = payload
        if code:
            self.code = code
        elif isinstance(payload, dict):
            self.code = (payload.get("error") or {}).get("code", "")
        else:
            self.code = ""


class LiveServer:
    """剪映进程当前暴露的 agent server 坐标."""

    def __init__(self, port: int, token: str, pid: int = 0,
                 bridge_port: int = 0, bridge_token: str = "",
                 raw: Optional[Dict[str, Any]] = None):
        self.port = int(port)
        self.token = str(token)
        self.pid = int(pid or 0)
        self.bridge_port = int(bridge_port or 0)
        self.bridge_token = str(bridge_token or "")
        self.raw = dict(raw or {})

    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def __repr__(self) -> str:
        return "<LiveServer port=%d pid=%s>" % (self.port, self.pid or "?")


def find_live_server(timeout: float = 6.0) -> LiveServer:
    """经 native bridge 的 JSB 取剪映**当前** agent server 的 port + token.

    依赖剪映正在运行; 抛 AgentRuntimeError 说明没找到。
    """
    from . import bridge as _bridge
    b = _bridge.Bridge(timeout=timeout)
    health = b.health()
    ctx = health.get("local_agent_server") or {}
    if not ctx.get("running") or not ctx.get("port") or not ctx.get("localServerToken"):
        raise AgentRuntimeError(
            "剪映的 local agent server 未在运行; health=%s" % json.dumps(health)[:200]
        )
    return LiveServer(
        port=ctx["port"], token=ctx["localServerToken"], pid=health.get("pid") or 0,
        bridge_port=b.port, bridge_token=b.token, raw=health,
    )


class AgentRuntimeClient:
    """附着到剪映自带 agent server 的同步客户端."""

    def __init__(self, server: Optional[LiveServer] = None,
                 timeout: float = DEFAULT_TIMEOUT):
        self.timeout = timeout
        self.server = server or find_live_server(timeout=min(timeout, 8.0))
        self.instance_id: str = ""
        self.session_id: str = ""
        self.last_run_id: str = ""
        self.last_task_id: str = ""
        self._seq = 0

    # ---- 底层 -------------------------------------------------------------
    def request(self, method: str, path: str, body: Any = None,
                bind: bool = True, timeout: Optional[float] = None
                ) -> Dict[str, Any]:
        """发一次请求, 返回解析后的 JSON (含 HTTP 状态)."""
        headers = {
            "x-custom-agent-token": self.server.token,
            "content-type": "application/json",
        }
        if bind:
            if self.instance_id:
                headers["x-agent-runtime-instance-id"] = self.instance_id
            if self.session_id:
                headers["x-agent-runtime-session-id"] = self.session_id
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self.server.base + path, data=data,
                                     headers=headers, method=method)
        t = timeout if timeout is not None else self.timeout
        try:
            with urllib.request.urlopen(req, timeout=t) as resp:
                raw = resp.read().decode("utf-8", "replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            status = e.code
        except Exception as e:                                   # noqa: BLE001
            raise AgentRuntimeError("连接 %s 失败: %s" % (self.server.base, e)) from e
        try:
            parsed: Any = json.loads(raw) if raw.strip() else {}
        except Exception:                                        # noqa: BLE001
            parsed = {"_raw": raw}
        return {"status": status, "body": parsed}

    def call(self, method: str, path: str, body: Any = None, bind: bool = True,
             timeout: Optional[float] = None) -> Any:
        r = self.request(method, path, body, bind=bind, timeout=timeout)
        if r["status"] >= 400:
            err = r["body"].get("error") if isinstance(r["body"], dict) else None
            msg = (err or {}).get("message") or json.dumps(r["body"])[:200]
            raise AgentRuntimeError("HTTP %d: %s" % (r["status"], msg),
                                    r["status"], r["body"])
        return r["body"]

    # ---- 控制平面 ---------------------------------------------------------
    def health(self) -> Dict[str, Any]:
        return self.call("GET", "/api/agent-runtime/v1/host/health",
                         bind=False, timeout=10)

    def resolve_instance(self, resource_id: str, resource_type: str = "canvas",
                         retention: str = "keep_alive") -> str:
        """建/取一个 runtime 实例, 记住 instanceId (201 = 新建)."""
        body = {
            "pluginId": "custom-agent",
            "resource": {"type": resource_type, "id": resource_id},
            "retention": {"mode": retention},
        }
        out = self.call("POST", "/api/agent-runtime/v1/instances:resolve", body,
                        bind=False)
        self.instance_id = ((out.get("instance") or {}).get("instanceId") or "")
        return self.instance_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        if not self.instance_id:
            raise AgentRuntimeError("先调用 resolve_instance()")
        out = self.call("GET", "/api/agent-runtime/v1/instances/%s/sessions"
                        % self.instance_id)
        return out.get("items") or []

    def ensure_session(self) -> str:
        """复用推荐会话; 没有就建一个."""
        if self.session_id:
            return self.session_id
        items = self.list_sessions()
        if items:
            self.session_id = items[0].get("sessionId") or ""
            return self.session_id
        out = self.call("POST", "/api/agent-runtime/v1/instances/%s/sessions"
                        % self.instance_id, {"title": "jyai"})
        self.session_id = ((out.get("session") or out) or {}).get("sessionId") or ""
        return self.session_id

    def bind(self, resource_id: str) -> "AgentRuntimeClient":
        """一步到位: resolve_instance + ensure_session."""
        self.resolve_instance(resource_id)
        self.ensure_session()
        return self

    def _rid(self, tag: str = "cmd") -> str:
        self._seq += 1
        return "jyai-%s-%d-%s" % (tag, self._seq, uuid.uuid4().hex[:8])

    # ---- 运行时指令 -------------------------------------------------------
    def command(self, command: Dict[str, Any], compact: bool = True,
                request_id: Optional[str] = None,
                response_view: Optional[str] = None) -> Dict[str, Any]:
        """POST /api/custom-agent/commands。返回原始响应体。"""
        body: Dict[str, Any] = {
            "requestId": request_id or self._rid("cmd"),
            "command": command,
        }
        if response_view:
            body["responseView"] = response_view
        elif compact:
            body["compact"] = True
        return self.call("POST", "/api/custom-agent/commands", body)

    def submit_task(self, task: str, agent_id: str = "v2-director-agent",
                    workspace_path: Optional[str] = None,
                    material_directory: Optional[str] = None,
                    direct_output: Optional[bool] = None) -> Dict[str, Any]:
        """提交一个创作任务 (相当于官方面板里发一句话)."""
        cmd: Dict[str, Any] = {
            "kind": "SubmitTask",
            "taskDescription": task,
            "agentId": agent_id,
        }
        if workspace_path:
            cmd["workspacePath"] = workspace_path
        if material_directory:
            cmd["materialDirectory"] = material_directory
        if direct_output is not None:
            cmd["directOutput"] = bool(direct_output)
        return self.command(cmd)

    def advance(self, run_id: str, reason: str = "jyai") -> Dict[str, Any]:
        return self.command({"kind": "AdvanceRun", "runId": run_id,
                             "reason": reason})

    def run_next_step(self, run_id: str) -> Dict[str, Any]:
        return self.command({"kind": "RunNextStep", "runId": run_id})

    def send_message(self, message: str, run_id: str) -> Dict[str, Any]:
        return self.command({"kind": "SendUserMessage", "message": message,
                             "runId": run_id})

    def open_run(self, run_id: str) -> Dict[str, Any]:
        return self.command({"kind": "OpenWorkspaceRun", "runId": run_id},
                            response_view="full")

    def available_commands(self, run_id: str) -> List[str]:
        out = self.open_run(run_id)
        result = out.get("result") or out.get("activeRun") or {}
        cmds = result.get("availableCommands") or []
        return [c.get("commandId") or c.get("id") or c.get("kind") or str(c)
                for c in cmds]

    def dispatch_available(self, run_id: str, command_id: str) -> Dict[str, Any]:
        return self.command({"kind": "DispatchAvailableCommand",
                             "runId": run_id, "commandId": command_id})

    @staticmethod
    def extract_run_id(resp: Dict[str, Any]) -> str:
        rs = resp.get("resultSummary") or {}
        if rs.get("runId"):
            return rs["runId"]
        summary = resp.get("summary") or {}
        if summary.get("activeRunId"):
            return summary["activeRunId"]
        result = resp.get("result") or {}
        return result.get("runId") or ""

    @staticmethod
    def run_view(resp: Dict[str, Any]) -> Dict[str, Any]:
        return resp.get("result") or resp.get("activeRun") or {}

    # ---- 工作区 (素材落地 + 建任务) ----------------------------------------
    def prepare_workspace(self) -> str:
        """POST /tasks/workspace/prepare -> 该会话专属 workspacePath (并建目录)."""
        out = self.call("POST", "/api/custom-agent/tasks/workspace/prepare", {})
        wp = out.get("workspacePath") or ""
        if wp:
            try:
                import os as _os
                _os.makedirs(wp, exist_ok=True)
            except OSError:
                pass
        return wp

    def register_local_paths(self, paths, workspace_path=None,
                             source: str = "server-picker",
                             publish: bool = False) -> List[Dict[str, Any]]:
        """把一个或多个本地文件登记为 runtime 素材, 返回 asset 列表.

        实测要点 (11.5.0.14471):
          * body 必须是 {"entries":[{path,entryType,...}], workspacePath, ...}
          * `entryType` 只接受 "file" / "folder"; **folder 会被原样拷贝**,
            目录含大量文件时容易 `local_input_workspace_copy_failed`,
            因此这里默认**自行展开**成逐个文件登记。
          * 登记会把文件**拷贝**进会话 workspace 的 assets/imports 下。
        """
        import os as _os
        if workspace_path is None:
            workspace_path = self.prepare_workspace()
        entries: List[Dict[str, Any]] = []
        for raw in ([paths] if isinstance(paths, str) else list(paths)):
            p = _os.path.abspath(str(raw))
            if _os.path.isdir(p):
                for root, _dirs, files in _os.walk(p):
                    for f in files:
                        if f.lower().endswith(MEDIA_EXTS):
                            fp = _os.path.join(root, f)
                            entries.append({"path": fp, "entryType": "file",
                                            "sizeBytes": _os.path.getsize(fp)})
            elif _os.path.isfile(p):
                entries.append({"path": p, "entryType": "file",
                                "sizeBytes": _os.path.getsize(p)})
        if not entries:
            raise AgentRuntimeError("没有可登记的素材文件: %r" % (paths,))
        body = {
            "source": source,
            "batchId": self._rid("batch"),
            "publishToAssetManagement": bool(publish),
            "workspacePath": workspace_path,
            "entries": entries,
        }
        out = self.call("POST", "/api/custom-agent/local-inputs/register-paths", body)
        return out.get("assets") or []

    def create_workspace_task(self, task_text: str, assets,
                              agent_id: str = "v2-director-agent",
                              workspace_path: Optional[str] = None,
                              material_directory: Optional[str] = None,
                              direct_output: bool = True,
                              title: Optional[str] = None) -> Dict[str, Any]:
        """POST /tasks/workspace -> 建一个带素材的创作任务 (官方 Hub 同款入口).

        `assets` 是 register_local_paths() 的返回值 (或它们的精简 ref 列表)。
        """
        if workspace_path is None:
            workspace_path = self.prepare_workspace()
        refs = []
        for a in assets or []:
            if isinstance(a, str):
                refs.append(a)
            else:
                refs.append(a)
        body: Dict[str, Any] = {
            "agentId": agent_id,
            "taskText": task_text,
            "assetRefs": refs,
            "workspacePath": workspace_path,
            "directOutput": bool(direct_output),
        }
        if title:
            body["title"] = title
        if material_directory:
            body["materialDirectory"] = material_directory
        out = self.call("POST", "/api/custom-agent/tasks/workspace", body)
        task = out.get("task") or {}
        if task.get("runId"):
            self.last_run_id = task["runId"]
            self.last_task_id = task.get("taskId") or ""
        return out

    def tool_logs(self, limit: int = 80) -> List[Dict[str, Any]]:
        """GET /logs -> 本会话的服务端日志条目."""
        out = self.call("GET", "/api/custom-agent/logs?limit=%d" % int(limit))
        return out.get("logs") or []

    # ---- 产品平面 (只读) --------------------------------------------------
    def skills(self) -> List[Dict[str, Any]]:
        return (self.call("GET", "/api/custom-agent/skills", bind=False) or {}).get("skills") or []

    def latest_skills(self) -> List[Dict[str, Any]]:
        return ((self.call("GET", "/api/custom-agent/skills/mentionable") or {})
                .get("skills") or [])

    def processors(self) -> List[Dict[str, Any]]:
        return (self.call("GET", "/api/processors", bind=False) or {}).get("processors") or []

    def workflows(self) -> Dict[str, Any]:
        return self.call("GET", "/api/custom-agent/workflows")

    def state_summary(self) -> Dict[str, Any]:
        return self.call("GET", "/api/custom-agent/runtime/state/summary")

    def capabilities(self) -> Dict[str, Any]:
        """host-capabilities/manifest。

        注意: 该路由要求**进程级 host capability token** (由 runtime 为它拉起的
        子进程签发, 见 app-server.mjs 的 createGrant)，外部客户端用
        x-custom-agent-token 只能换到 401 invalid_host_capability_token。
        这里如实回报实际错误, 不伪造。
        """
        return self.call("GET", "/api/custom-agent/host-capabilities/manifest")


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
