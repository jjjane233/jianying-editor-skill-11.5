"""jyai -- 剪映 (JianyingPro) PC 版逆向接口.

对外能力
--------
    草稿读写      Draft / list_drafts / find_draft
    造草稿        engine.build_from_scratch(spec, out_dir)
    改草稿        engine.patch_existing(draft_dir, edits)
    注册列表      project.register / write_timelines
    加密开关      settings_patch.disable / enable / read_state
    密钥库        keystore.load / save / add_material_key
    配置库        mmkv.read_records / get_json / set_json
    素材探测      util_jy.probe_media / probe_audio
    AI Agent      agent.DeepAgent (deepagents_capi.dll 原生绑定)
    ★ 画质        quality.apply / plan   超清画质 + 补分辨率 + 一键超清

    ★ 进程内桥    bridge.Bridge      无限画布 native HTTP 桥 (JSB)
    ★ AI 画布     canvas.CanvasProtocol   AI Creation Canvas 协作协议
    ★ 后端拉起    appserver.start     启动剪映自带 CustomAgent app-server
    ★ 官方运行时  agentruntime        附着剪映进程的 CustomAgent 运行时 (带登录态)
"""
from . import draft, engine, keystore, mmkv, project, quality, settings_patch, util_jy
from .draft import Draft, find_draft, list_drafts

try:
    from . import bridge
except Exception:           # 仅在依赖缺失时降级, 不阻塞其它功能
    bridge = None

try:
    from . import canvas
except Exception:
    canvas = None

try:
    from . import appserver
except Exception:
    appserver = None

try:
    from . import agentruntime
except Exception:
    agentruntime = None

__version__ = "1.3.0"

__all__ = [
    "Draft", "list_drafts", "find_draft",
    "draft", "engine", "keystore", "mmkv", "project", "quality", "settings_patch", "util_jy",
    "bridge", "canvas", "appserver", "agentruntime",
    "__version__",
]
