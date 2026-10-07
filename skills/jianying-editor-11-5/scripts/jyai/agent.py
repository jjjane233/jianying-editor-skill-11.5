"""对接剪映自带的 AI 剪辑引擎.

剪映 11.x 里有两条"AI 帮剪辑"的原生通道, 逆向结果如下.

通道 A -- deepagents_capi.dll (Go 写的 Agent 运行时, 纯本地 C ABI)
-----------------------------------------------------------------
实测导出 83~86 个 da_* 符号, 关键几个:
    da_get_version()                     -> "dev"
    da_get_abi_version()                 -> 27
    da_get_api_table(abi)                -> 函数表指针 (传 27 才有值)
    da_agent_create(cfg, ...)            -> agent 句柄
    da_agent_create_with_callbacks(...)  -> 带事件回调创建
    da_agent_start(agent, ...)           -> 起一轮
    da_agent_run(agent, ...)             -> 同步跑
    da_agent_run_async / da_agent_stream_start / da_agent_stream_run
    da_agent_next(stream)                -> 逐条取事件
    da_agent_get_conversation / _history_page / _execution_graph
    da_agent_execute_subagent            -> 子 agent
    da_agent_retrieve_deeplinks          -> 取可点跳转链接
    da_session_set_event_callbacks / set_prompt_injections /
        set_thinking_override / list_raw_transcript_events
    da_tool_spec_array_alloc/free        -> 自定义工具
    da_tool_call_reply_impl              -> 工具回执

字符串里能看到 Go 侧结构名: system_prompt / visible_tools / approve_tools /
enable_tools / tool_resultspayload / summarization / sub_agent / SKILL.yml /
tool_compress / checkpoint_id / llm_fallback / model_name / base_url / api_key /
doubao / deepseek / ARK_BASE_URL / KIMI_API_KEY ...
=> 它是一个可独立驱动的大模型 Agent 运行时, 模型可指向豆包/DeepSeek 等.

通道 B -- VEHelper.exe 内的 HTTP 路由
-----------------------------------------------------------------
    /agent_edit_api/retrieve_tools
    /agent_edit_api/query_skills
    /agent_edit_api/run_draft_modify_intent     <-- "按意图改草稿"
    /agent_edit_api/retrieve_deeplinks
    /agent_edit_api/answer_faq
    /agent_edit_api/run_model_gate
    /agent_edit_api/run_model_classify
    /agent_edit_api/rough_cut_plan_generation   <-- 粗剪方案生成
    /agent_edit_api/talking_head_script_generation
    /agent_edit_api/link_extract/pc
    /agent_edit_api/copilot_sug
    /agent_edit_api/common_task/{new,query,cancel}
    /lv/v1/copilot/conversation/history
    /lv/v1/copilot/section/history
其中 run_draft_modify_intent 就是"给一句话, 让它改当前草稿"的入口.

本模块提供:
    DeepAgent      -- 通道 A 的 ctypes 封装 (da_* 全量导出可达)
    api_routes()   -- 通道 B 的路由表
    modify_intent()-- 走通道 B 的 run_draft_modify_intent 请求构造
"""
from __future__ import annotations
import ctypes, os, json, time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# 通道 A
# --------------------------------------------------------------------------
DEFAULT_DLL = os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "Apps")


def find_dll(apps_dir: Optional[str] = None) -> Optional[str]:
    """在 Apps/<version>/ 下找最新的 deepagents_capi.dll."""
    root = apps_dir or DEFAULT_DLL
    if not os.path.isdir(root):
        return None
    best = None
    for d in os.listdir(root):
        p = os.path.join(root, d, "deepagents_capi.dll")
        if os.path.isfile(p):
            ver = []
            for part in d.replace("_", ".").split("."):
                ver.append(int(part) if part.isdigit() else 0)
            best = p if best is None or ver > best[0] else best
            best = (ver, p) if isinstance(best, tuple) else (ver, p)
    return best[1] if isinstance(best, tuple) else best


class DeepAgent:
    """deepagents_capi.dll 的 ctypes 封装.

    注意: 该 DLL 是 Go 运行时, 首次加载会拉起 GC 线程,
    必须在剪映安装目录之外单独开进程调用 (直接 import 到宿主进程也可,
    但进程退出时 Go runtime 可能不干净, 建议子进程隔离).
    """

    def __init__(self, dll: Optional[str] = None):
        path = dll or find_dll()
        if not path or not os.path.isfile(path):
            raise FileNotFoundError("找不到 deepagents_capi.dll, 请传 dll= 参数")
        self.path = path
        self.dir = os.path.dirname(path)
        try:
            os.add_dll_directory(self.dir)
        except (AttributeError, OSError):
            pass
        self.lib = ctypes.CDLL(path)
        self._bind()

    # ---- 基础 ----
    def _bind(self) -> None:
        L = self.lib
        L.da_get_version.restype = ctypes.c_char_p
        L.da_get_abi_version.restype = ctypes.c_int
        L.da_last_error_message.restype = ctypes.c_char_p
        L.da_get_api_table.restype = ctypes.c_void_p
        L.da_get_api_table.argtypes = [ctypes.c_int]
        for n in ("da_agent_create", "da_agent_create_with_callbacks",
                  "da_agent_start", "da_agent_run", "da_agent_run_async",
                  "da_agent_stream_start", "da_agent_stream_run",
                  "da_agent_stream_next", "da_agent_next",
                  "da_agent_get_conversation", "da_agent_list_conversations",
                  "da_agent_get_conversation_history_page",
                  "da_agent_get_conversation_execution_graph",
                  "da_agent_execute_subagent", "da_agent_retrieve_deeplinks",
                  "da_agent_destroy", "da_agent_recover",
                  "da_session_create", "da_session_destroy",
                  "da_session_set_event_callbacks", "da_session_set_prompt_injections",
                  "da_session_set_thinking_override", "da_session_list_raw_transcript_events",
                  "da_tool_spec_array_alloc", "da_tool_spec_array_free",
                  "da_buffer_alloc", "da_buffer_free",
                  "da_task_cancel", "da_task_resume", "da_task_destroy"):
            f = getattr(L, n, None)
            if f is not None:
                f.restype = ctypes.c_void_p

    @property
    def version(self) -> str:
        v = self.lib.da_get_version()
        return v.decode() if v else ""

    @property
    def abi(self) -> int:
        return int(self.lib.da_get_abi_version())

    def api_table(self, abi: Optional[int] = None) -> int:
        """取函数表指针. 实测只接受本机 ABI (27), 其它入参返回 0."""
        a = self.abi if abi is None else abi
        return int(self.lib.da_get_api_table(a) or 0)

    def last_error(self) -> str:
        v = self.lib.da_last_error_message()
        return v.decode() if v else ""

    def exports(self) -> List[str]:
        """列出该 DLL 的全部 da_* 导出."""
        import pefile
        pe = pefile.PE(self.path, fast_load=True)
        pe.parse_data_directories(
            directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_EXPORT"]])
        exp = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
        if not exp:
            return []
        return sorted(e.name.decode() for e in exp.symbols
                      if e.name and e.name.decode().startswith("da_"))

    def describe(self) -> Dict[str, Any]:
        return {"dll": self.path, "version": self.version, "abi": self.abi,
                "api_table": hex(self.api_table()), "exports": len(self.exports())}


# --------------------------------------------------------------------------
# 通道 B
# --------------------------------------------------------------------------
ROUTES = [
    "/agent_edit_api/run_draft_modify_intent",
    "/agent_edit_api/rough_cut_plan_generation",
    "/agent_edit_api/retrieve_tools",
    "/agent_edit_api/query_skills",
    "/agent_edit_api/retrieve_deeplinks",
    "/agent_edit_api/answer_faq",
    "/agent_edit_api/run_model_gate",
    "/agent_edit_api/run_model_classify",
    "/agent_edit_api/copilot_sug",
    "/agent_edit_api/link_extract/pc",
    "/agent_edit_api/common_task/new",
    "/agent_edit_api/common_task/query",
    "/agent_edit_api/common_task/cancel",
    "/agent_edit_api/talking_head_script_generation",
    "/lv/v1/copilot/conversation/history",
    "/lv/v1/copilot/section/history",
]


def api_routes() -> List[str]:
    return list(ROUTES)


def modify_intent(prompt: str, draft_id: str = "", timeline_id: str = "",
                  app_version: str = "11.3.0", **extra: Any) -> Dict[str, Any]:
    """构造 /agent_edit_api/run_draft_modify_intent 的请求体.

    剪映本体的请求头实测包含 appid / device-time / tdid / sign-ver /
    App-Sdk-Version / x-tt-env 等, 其中 sign 由 sscronet/ever_cloud_sdk 侧签名,
    纯外部复现需要复用进程内的签名器; 本函数只负责请求体, 头由调用方补.
    """
    body = {
        "draft_id": draft_id,
        "timeline_id": timeline_id,
        "intent": prompt,
        "app_version": app_version,
        "platform": "windows",
        "ts": int(time.time() * 1000),
    }
    body.update(extra)
    return body


def headers(appid: str = "3704", tdid: str = "", sign_ver: str = "",
            sdk_version: str = "48.0.0", env: str = "") -> Dict[str, str]:
    h = {"Content-Type": "application/json",
         "appid": appid,
         "device-time": str(int(time.time() * 1000)),
         "App-Sdk-Version": sdk_version}
    if tdid:
        h["tdid"] = tdid
    if sign_ver:
        h["sign-ver"] = sign_ver
    if env:
        h["x-tt-env"] = env
    return h


if __name__ == "__main__":
    import sys
    d = find_dll(sys.argv[1] if len(sys.argv) > 1 else None)
    print("dll:", d)
    if d:
        a = DeepAgent(d)
        print(json.dumps(a.describe(), ensure_ascii=False, indent=2))
