"""启动剪映自带的 CustomAgent app-server (AI 剪辑后端).

为什么需要它
------------
剪映进程内自带的 canvas bridge (port/token 在
`%TEMP%\\infinite-canvas-native-bridge.json`) 只暴露一层 JSB 桥,
能力有限(建草稿/取上下文).  真正干活的 "AI 剪辑" 后端是随包发布的
`Resources/canvas_agent/runtime/app-server.mjs`, 由 bun 承载, 默认
并不会自动拉起 —— 这里用逆向出的参数组合把它拉起来.

逆向要点 (全部实测于 11.5.0.14471)
-----------------------------------
* 运行时: `<Apps>/<ver>/bun.exe` (1.3.14)
* 入口  : `<canvas_agent>/runtime/app-server.mjs --host 127.0.0.1 --port N`
* **不要设 `NODE_ENV=production`** —— 会让编辑协议走 production 分支,
  强制要求 frozen manifest (`editing-protocol/production-binding.json`,
  该文件不存在), 从而抛错拒绝启动.  让它走 development fallback,
  只要 `lyra-cli` 存在即可通过.
* 必须提供 `lyra-cli`: `<Apps>/<ver>/Resources/agent_runtime_env/bin/lyra-cli.cmd`
  (内部转发到 `<Apps>/<ver>/lyra-cli.exe`), 通过
  `CUSTOM_AGENT_LYRA_CLI_COMMAND` 指过去.
* `CUSTOM_AGENT_RESOURCE_ROOT` 必须指向 canvas_agent 目录, development
  fallback 靠它找 `skills/studio.lyra.xml_timeline_builder`.
* native addon (`agent_runtime_addon.node` / `videoeditor_addon.node`)
  在 **应用根目录** `<Apps>/<ver>/`, 不在 runtime 下.
* 不设 `CUSTOM_AGENT_SERVER_TOKEN` 时, 本地 API 只校验
  `x-custom-agent-token` 头非空即可, 省事.

启动后:
    GET  /api/agent-runtime/v1/host/health
    POST /api/agent-runtime/v1/instances:resolve
    ... 见 README 的 API 表
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

__all__ = [
    "find_app_dir", "find_bun", "find_canvas_agent", "build_env",
    "start", "ServerHandle", "DEFAULT_PORT",
]

DEFAULT_PORT = 54397


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base)
    return Path.home() / "AppData" / "Local"


def find_app_dir(explicit: Optional[str] = None) -> Path:
    """定位剪映应用目录 (含 bun.exe / VECreator.dll / JianyingPro.exe)."""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        raise FileNotFoundError("app dir not found: %s" % explicit)

    apps = _local_appdata() / "JianyingPro" / "Apps"
    if not apps.exists():
        raise FileNotFoundError("JianyingPro Apps not found: %s" % apps)

    cands: List[Path] = []
    for d in apps.iterdir():
        if not d.is_dir():
            continue
        has_bun = (d / "bun.exe").exists()
        has_ca = (d / "Resources" / "canvas_agent" / "runtime" / "app-server.mjs").exists()
        if has_bun and has_ca:
            cands.append(d)
    if not cands:
        raise FileNotFoundError(
            "no JianyingPro app dir with bun.exe + canvas_agent; checked %s" % apps)
    # 版本号排序, 取最高
    def key(p: Path):
        try:
            return tuple(int(x) for x in p.name.split("."))
        except ValueError:
            return (0,)
    cands.sort(key=key)
    return cands[-1]


def find_bun(app_dir: Path) -> Path:
    p = app_dir / "bun.exe"
    if not p.exists():
        raise FileNotFoundError("bun.exe not found: %s" % p)
    return p


def find_canvas_agent(app_dir: Path) -> Path:
    p = app_dir / "Resources" / "canvas_agent"
    if not (p / "runtime" / "app-server.mjs").exists():
        raise FileNotFoundError("canvas_agent runtime not found: %s" % p)
    return p


def find_lyra_cli(app_dir: Path) -> Optional[Path]:
    for rel in ("Resources/agent_runtime_env/bin/lyra-cli.cmd",
                "lyra-cli.exe",
                "Resources/agent_runtime_env/bin/lyra-cli.exe"):
        p = app_dir / rel
        if p.exists():
            return p
    return None


def build_env(app_dir: Path, canvas_agent: Path, state_dir: Path,
              port: int = DEFAULT_PORT) -> Dict[str, str]:
    """构造 app-server 需要的环境变量 (逆向自 startProcess + 实测定型)."""
    env = dict(os.environ)
    # 关键: 清掉 production, 否则要求 frozen manifest 而启动失败
    env.pop("NODE_ENV", None)

    lyra = find_lyra_cli(app_dir)
    ca = str(canvas_agent)

    env.update({
        "AGENT_HOST_APP_DIR": ca + r"\app",
        "AGENT_HOST_PACKAGE_ROOT": ca,
        "CUSTOM_AGENT_PACKAGE_ROOT": ca,
        "AGENT_HOST_RESOURCE_ROOT": ca,
        "CUSTOM_AGENT_RESOURCE_ROOT": ca,
        "AGENT_HOST_STATE_DIR": str(state_dir),
        "CUSTOM_AGENT_STATE_DIR": str(state_dir),
        # 跳过需要登录态的远程 skill 拉取
        "CUSTOM_AGENT_LOCAL_SKILL_DEBUG": "1",
        "CUSTOM_AGENT_DISABLE_PROCESS_SANDBOX": "true",
        "AGENT_RUNTIME_ADDON_PATH": str(app_dir / "agent_runtime_addon.node"),
        "VIDEOEDITOR_ADDON_PATH": str(app_dir / "videoeditor_addon.node"),
        "CUSTOM_AGENT_NATIVE_ADDON_DIR": str(app_dir),
        "AGENT_RUNTIME_ADDON_ENABLED": "1",
        "PORT": str(port),
    })
    if lyra:
        env["CUSTOM_AGENT_LYRA_CLI_COMMAND"] = str(lyra)
    return env


class ServerHandle:
    """已启动的 app-server 句柄."""

    def __init__(self, proc: subprocess.Popen, port: int, log_path: Path,
                 app_dir: Path, canvas_agent: Path):
        self.proc = proc
        self.port = port
        self.log_path = log_path
        self.app_dir = app_dir
        self.canvas_agent = canvas_agent

    @property
    def pid(self) -> int:
        return self.proc.pid

    def alive(self) -> bool:
        return self.proc.poll() is None

    def log(self, tail: int = 4000) -> str:
        try:
            return self.log_path.read_text("utf-8", "replace")[-tail:]
        except OSError:
            return ""

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    import socket
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start(port: int = DEFAULT_PORT, app_dir: Optional[str] = None,
          state_dir: Optional[str] = None, log_path: Optional[str] = None,
          wait: float = 40.0, wait_ready: bool = True) -> ServerHandle:
    """拉起 app-server, 默认阻塞等待端口就绪.

    :param wait: 最多等待秒数; 端口就绪即立刻返回
    :param wait_ready: False 则只做非阻塞拉起
    """
    # 端口已被占用: 视为已经有实例在跑, 直接复用
    if _port_open(port):
        raise RuntimeError(
            "port %d is already in use -- an app-server is probably already "
            "running. Reuse it, or pick another port with --port." % port)

    ad = find_app_dir(app_dir)
    bun = find_bun(ad)
    ca = find_canvas_agent(ad)

    if state_dir is None:
        state_dir = Path(os.environ.get("TEMP", ".")) / "jyai_agent_state"
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)

    if log_path is None:
        log_path = state_dir / ("app-server-%d.log" % port)
    log_path = Path(log_path)
    log_fp = open(log_path, "wb")

    env = build_env(ad, ca, state_dir, port)
    proc = subprocess.Popen(
        [str(bun), str(ca / "runtime" / "app-server.mjs"),
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ca), env=env, stdout=log_fp, stderr=subprocess.STDOUT)

    handle = ServerHandle(proc, port, log_path, ad, ca)
    if not wait_ready:
        return handle

    deadline = time.time() + wait
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                "app-server exited with code %s; log tail:\n%s"
                % (proc.returncode, handle.log()))
        if _port_open(port):
            return handle
        time.sleep(0.4)
    raise TimeoutError("app-server did not listen on %d within %.0fs; log:\n%s"
                       % (port, wait, handle.log()))


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="启动剪映 CustomAgent app-server")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--app-dir", default=None)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--wait", type=float, default=40.0)
    ap.add_argument("--foreground", action="store_true",
                    help="前台常驻, Ctrl+C 退出")
    a = ap.parse_args(argv)

    h = start(a.port, a.app_dir, a.state_dir, wait=a.wait)
    print("app-server listening  http://127.0.0.1:%d" % h.port)
    print("pid                   %d" % h.pid)
    print("bun                   %s" % (h.app_dir / "bun.exe"))
    print("canvas_agent          %s" % h.canvas_agent)
    print("log                   %s" % h.log_path)
    if a.foreground:
        try:
            while h.alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            h.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
