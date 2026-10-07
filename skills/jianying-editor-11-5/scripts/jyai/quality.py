"""剪映 11.5 画质能力: 超清画质 / 补分辨率 / 一键超清.

逆向结论 (Windows 剪映专业版 11.5.0.14471)
==========================================
画质能力分三层, 落点完全不同, 必须分开处理:

1) 片段级 "超清画质 / AI 画质提升"  --  pc_feature_image_enhance
   落点: draft_content.json -> materials.videos[].video_algorithm
         .quality_enhance = {"from": "multi_track", "level": N}
   等级: level=1 高清 / 2 超清 / 3 AI HD  (见 videoeditor.dll 迁移脚本
         downgrade_136_to_135_quality_ai_hd_level, level==3 即 AI HD)
   同时 draft 级记录一份 function_assistant_info:
         enhance_quality (bool) / enhance_quality_fixed (bool)
         enhance_quality_segid_list (list[segment_id])
   这些是**本地算法**(降噪/去频闪/锐化), 不排队、不上传, 便宜。

2) 片段级 "补分辨率"  --  pc_super_resolution / pc_export_super_resolution
   落点: 同上 video_algorithm.super_resolution
   这是**云端算法**: 素材要上传服务器排队处理
   (po: pc_enhance_quality_pop_up_agree 明说 "需将其上传到我们的服务器进行处理";
    dll: LYRA_CLI_UPLOAD_VID / upscale_resolution upload failed /
    super_resolution upload timeout; settings: super_resolution_config)
   单段时长上限 super_resolution_config.duration_limit (实测 30 秒)。

3) 草稿级 "一键超清" 导出开关  --  pc_m10n_export_ultra_hd_switch / pc_exp_ai_uhd
   落点: <draft>/attachment_editing.json
         -> editing_draft.is_use_one_click_ultra_hd = bool
   (以及 Timelines/<tid>/attachment_editing.json 同一字段)
   导出面板勾选状态另存于 User Data/Config/Export.ini [General]
         userFirstVideoEnhanceExport=true|false
   commonSetting.ini: quality_enhance_last_choose (档位记忆)
                      super_resolution_used (用过没)

【重要】对齐原生保存只改这几个 key, 不要重建整个文件。
写入后必须同步 Timelines 副本 + 首页登记, 否则剪映读到旧值。

【验证边界】片段级 super_resolution 的非空结构未在本机真实草稿中出现过
(全盘草稿扫描 0 例), 因此本模块按 settings_json 里
super_resolution_config.video_params / image_params 的同名参数构造,
可用 --dry-run 先看结构。开启后请在剪映 GUI 复核一次。
"""
from __future__ import annotations
import copy, json, os, time
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    from . import draft as _draft
    from . import util_jy as _u
except ImportError:  # 直接跑
    import draft as _draft
    import util_jy as _u

# ---------------------------------------------------------------------------
# 常量 (逆向自 po / dll / settings_json)
# ---------------------------------------------------------------------------
LEVELS = {"hd": 1, "高清": 1, "quality": 1, "1": 1,
          "uhd": 2, "超清": 2, "super": 2, "2": 2,
          "ai_hd": 3, "aihd": 3, "ai hd": 3, "aihd3": 3,
          "极清": 3, "ai极清": 3, "3": 3}
LEVEL_NAMES = {1: "高清", 2: "超清", 3: "AI HD"}

MODE_OFF = "off"
MODE_QUALITY = "quality_enhance"      # 本地
MODE_SR = "super_resolution"          # 云端
MODE_ULTRA = "one_click_ultra_hd"     # 导出开关

QE_OBJ = {"from": "multi_track", "level": 1}

# 与 settings_json:super_resolution_config.*_params 同名
SR_VIDEO_PARAMS = {"4x_mode": True, "cq": 1, "crf": 20, "gop": 30,
                   "level": 3, "log_level": 1, "preset": 1, "profile": 1}
SR_IMAGE_PARAMS = {"4x_mode": True, "level": 2, "log_level": 1}

ATT_REL = "attachment_editing.json"
ATT_TEMPLATE: Dict[str, Any] = {
    "editing_draft": {   'ai_remove_filter_words': {'enter_source': '', 'right_id': ''},
        'ai_shorts_info': {'report_params': '', 'type': 0},
        'cover_extra_info': {   'draft_id': '',
                                'position': 0,
                                'select_segment_id': '',
                                'select_segment_source_start': 0,
                                'select_segment_target_start': 0,
                                'slot_image_path': '',
                                'slot_info_config': {   'slot_image_path': '',
                                                        'used_video_algorithm_configs': []},
                                'type': 1,
                                'video_draft_source': -1},
        'crop_info_extra': {'crop_mirror_type': 0, 'crop_rotate': 0.0, 'crop_rotate_total': 0.0},
        'cutsame_to_image_edit_template_id': '',
        'digital_human_template_to_video_info': {'has_upload_material': False, 'template_type': 0},
        'draft_used_recommend_function': '',
        'edit_type': 0,
        'eye_correct_enabled_multi_face_time': 0,
        'has_adjusted_render_layer': False,
        'image_ai_chat_info': {   'before_chat_edit': False,
                                  'draft_modify_time': 0,
                                  'generate_type': '',
                                  'inspiration_item_id': '',
                                  'inspiration_item_name': '',
                                  'keyword_content': '',
                                  'keyword_id': '',
                                  'keyword_name': '',
                                  'keyword_type': '',
                                  'message_id': '',
                                  'model_name': '',
                                  'need_restore': False,
                                  'picture_id': '',
                                  'prompt_content': '',
                                  'prompt_from': '',
                                  'sugs_info': []},
        'image_ai_template_info': {   'first_draw_type': '',
                                      'inspiration_id': '',
                                      'item_type': '',
                                      'request_id': ''},
        'is_open_expand_player': False,
        'is_template_text_ai_generate': False,
        'is_use_adjust': False,
        'is_use_ai_expand': False,
        'is_use_ai_image': False,
        'is_use_ai_remove': False,
        'is_use_ai_video': False,
        'is_use_audio_separation': False,
        'is_use_chroma_key': False,
        'is_use_curve_speed': False,
        'is_use_digital_human': False,
        'is_use_edit_multi_camera': False,
        'is_use_lip_sync': False,
        'is_use_lock_object': False,
        'is_use_loudness_unify': False,
        'is_use_noise_reduction': False,
        'is_use_one_click_beauty': False,
        'is_use_one_click_ultra_hd': False,
        'is_use_retouch_face': False,
        'is_use_smart_adjust_color': False,
        'is_use_smart_body_beautify': False,
        'is_use_smart_motion': False,
        'is_use_subtitle_recognition': False,
        'is_use_text_to_audio': False,
        'material_edit_session': {'material_edit_info': [], 'session_id': '', 'session_time': 0},
        'paste_segment_list': [],
        'profile_entrance_type': '',
        'publish_enter_from': '',
        'publish_type': '',
        'single_function_type': 0,
        'text_convert_case_types': [],
        'version': '1.0.0',
        'video_recording_create_draft': ''},
}


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------
def user_data_dir() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
        "JianyingPro", "User Data")


def _read_json(p: str) -> Optional[Dict[str, Any]]:
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "rb") as f:
            return json.loads(f.read().decode("utf-8-sig"))
    except Exception:
        try:
            return _draft.load_json(p)
        except Exception:
            return None


def _write_json(p: str, obj: Dict[str, Any]) -> None:
    from .repair import atomic_bytes
    atomic_bytes(p, json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def normalize_level(level: Any) -> int:
    if isinstance(level, bool):
        return 2
    if isinstance(level, int):
        return max(1, min(3, level))
    if isinstance(level, str):
        k = level.strip().lower()
        if k in LEVELS:
            return LEVELS[k]
        if k.isdigit():
            return max(1, min(3, int(k)))
    return 2


# ---------------------------------------------------------------------------
# attachment_editing.json
# ---------------------------------------------------------------------------
def att_paths(draft_dir: str) -> List[str]:
    d = os.path.abspath(draft_dir)
    out = [os.path.join(d, ATT_REL)]
    tl = os.path.join(d, "Timelines")
    if os.path.isdir(tl):
        for tid in sorted(os.listdir(tl)):
            tdir = os.path.join(tl, tid)
            if os.path.isdir(tdir):
                out.append(os.path.join(tdir, ATT_REL))
    return out


def read_ultra_hd(draft_dir: str) -> Optional[bool]:
    j = _read_json(os.path.join(os.path.abspath(draft_dir), ATT_REL))
    if not j:
        return None
    return (j.get("editing_draft") or {}).get("is_use_one_click_ultra_hd")


def _sibling_att_template(draft_dir: str) -> Dict[str, Any]:
    """优先用同一草稿里已存在的 attachment_editing.json 当模板。

    剪映不同小版本字段集会增删 (实测 11.5.0.14471 根级 45 键 / 旧草稿 43 键)。
    克隆同草稿已有结构, 比硬编码模板更贴近原生, 也不会丢掉未知字段。
    """
    for p in att_paths(draft_dir):
        j = _read_json(p)
        if j and isinstance(j.get("editing_draft"), dict):
            return copy.deepcopy(j)
    return copy.deepcopy(ATT_TEMPLATE)


def set_ultra_hd(draft_dir: str, enabled: bool, create: bool = True) -> List[str]:
    """写 is_use_one_click_ultra_hd 到草稿根 + Timelines 各副本。

    只改这一个 key, 其余字段原样保留 (原生保存语义)。
    """
    paths = att_paths(draft_dir)
    # 先在**任何写入之前**把已有结构和模板都取好:
    # 否则给"根级"补出文件后, 再给 "Timelines 副本" 取模板时会读到刚写的那个,
    # 发现值已经对了就跳过, 副本永远建不出来。
    existing = {p: _read_json(p) for p in paths}
    seed = copy.deepcopy(ATT_TEMPLATE)
    for p in paths:
        j = existing.get(p)
        if j and isinstance(j.get("editing_draft"), dict) and len(j["editing_draft"]) > len(seed["editing_draft"]):
            seed = copy.deepcopy(j)

    touched: List[str] = []
    for p in paths:
        j = existing.get(p)
        if j is None:
            if not create:
                continue
            j = copy.deepcopy(seed)
        ed = j.setdefault("editing_draft", {})
        if ed.get("is_use_one_click_ultra_hd") == bool(enabled):
            continue                     # 已是目标值, 不写、也不回报
        ed["is_use_one_click_ultra_hd"] = bool(enabled)
        _write_json(p, j)
        touched.append(p)
    return touched


# ---------------------------------------------------------------------------
# 片段级 (draft_content.json)
# ---------------------------------------------------------------------------
def iter_videos(content: Dict[str, Any]):
    for m in (content.get("materials") or {}).get("videos", []) or []:
        yield m


def segment_ids_of_material(content: Dict[str, Any], material_id: str) -> List[str]:
    out = []
    for tr in content.get("tracks", []) or []:
        for s in tr.get("segments", []) or []:
            if s.get("material_id") == material_id:
                out.append(s.get("id"))
    return [x for x in out if x]


def read_segment_quality(content: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for m in iter_videos(content):
        va = m.get("video_algorithm") or {}
        qe = va.get("quality_enhance")
        sr = va.get("super_resolution")
        if qe or sr:
            rows.append({
                "material_id": m.get("id"),
                "name": m.get("material_name") or os.path.basename(m.get("path") or ""),
                "width": m.get("width"), "height": m.get("height"),
                "type": m.get("type"),
                "quality_enhance": qe, "super_resolution": sr,
            })
    return rows


def apply_to_segments(content: Dict[str, Any], mode: str, level: int = 2,
                      only_material_ids: Optional[Sequence[str]] = None,
                      make_sr_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把画质能力写到 video_algorithm。返回变更统计。"""
    mats = content.setdefault("materials", {})
    mats.setdefault("videos", [])
    want = set(only_material_ids) if only_material_ids else None
    n_qe = n_sr = 0
    seg_ids: List[str] = []

    # 同草稿里若已有更完整的 video_algorithm, 拿它当骨架, 避免只写两个键
    seed: Dict[str, Any] = {}
    for m in mats["videos"]:
        cand = m.get("video_algorithm")
        if isinstance(cand, dict) and len(cand) > len(seed):
            seed = copy.deepcopy(cand)

    for m in mats["videos"]:
        if want is not None and m.get("id") not in want:
            continue
        if not isinstance(m.get("video_algorithm"), dict):
            m["video_algorithm"] = copy.deepcopy(seed) if seed else {"path": ""}
        va = m["video_algorithm"]
        va.setdefault("quality_enhance", None)
        va.setdefault("super_resolution", None)
        if mode in (MODE_QUALITY, MODE_SR):
            qe = dict(QE_OBJ)
            qe["level"] = normalize_level(level)
            va["quality_enhance"] = qe
            n_qe += 1
            seg_ids.extend(segment_ids_of_material(content, m.get("id")))
        if mode == MODE_SR:
            sr = {"from": "multi_track"}
            is_img = (m.get("type") == "photo") or not (
                (m.get("duration") or 0) > 0 and m.get("has_audio") is not None)
            sr.update(SR_IMAGE_PARAMS if m.get("type") == "photo" else SR_VIDEO_PARAMS)
            if make_sr_params:
                sr.update(make_sr_params)
            va["super_resolution"] = sr
            n_sr += 1
    return {"quality_enhance": n_qe, "super_resolution": n_sr,
            "segid_list": sorted(set(seg_ids))}


def update_function_assistant(content: Dict[str, Any], enable: bool,
                              seg_ids: Optional[Sequence[str]] = None) -> bool:
    """维护 draft 级 function_assistant_info 记录。"""
    fa = content.get("function_assistant_info")
    if fa is None:
        fa = content["function_assistant_info"] = {"fps": {"den": 1, "num": 0}}
    fa.setdefault("enhance_quality_segid_list", [])
    fa["enhance_quality"] = bool(enable)
    fa["enhance_quality_fixed"] = False
    if seg_ids is not None:
        fa["enhance_quality_segid_list"] = sorted(set(seg_ids))
    return True


# ---------------------------------------------------------------------------
# 导出面板 (User Data/Config)
# ---------------------------------------------------------------------------
EXPORT_INI = "Export.ini"
COMMON_INI = "commonSetting.ini"
GLOBAL_INI = "globalSetting"


def _read_ini(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return out
    for line in txt.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _write_ini(path: str, values: Dict[str, str], section: str = "General") -> str:
    merged = _read_ini(path)
    merged.update(values)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    lines = ["[%s]" % section]
    for k, v in merged.items():
        lines.append("%s=%s" % (k, v))
    body = "\r\n".join(lines) + "\r\n"
    from .repair import atomic_bytes
    atomic_bytes(path, body.encode("utf-8"))
    return path


def jianying_running() -> bool:
    """剪映**主程序**是否在跑。

    写 User Data/Config/*.ini 期间, 主程序会用自己的内存态回写,
    所以必须等它退出。托盘助手 JianyingProTray.exe 不算 —— 它会一直常驻。
    """
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x2

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD),
                        ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", ctypes.c_char * 260)]

        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if snap == -1:
            return False
        try:
            e = PE32(); e.dwSize = ctypes.sizeof(PE32)
            ok = k32.Process32First(snap, ctypes.byref(e))
            while ok:
                nm = e.szExeFile.decode("mbcs", "ignore").lower()
                # 只认主程序 JianyingPro.exe。
                # JianyingProTray.exe 是常驻托盘的助手, 关掉主窗口后它仍在,
                # 若把它也算作"运行中", 面板写入会被永久挡住。
                if nm == "jianyingpro.exe":
                    return True
                ok = k32.Process32Next(snap, ctypes.byref(e))
        finally:
            k32.CloseHandle(snap)
    except Exception:
        return False
    return False


def read_export_panel(user_data: Optional[str] = None) -> Dict[str, Any]:
    ud = user_data or user_data_dir()
    cfg = os.path.join(ud, "Config")
    return {
        "export_ini": _read_ini(os.path.join(cfg, EXPORT_INI)),
        "common": _read_ini(os.path.join(cfg, COMMON_INI)),
        "global": _read_ini(os.path.join(cfg, GLOBAL_INI)),
    }


def set_export_panel(user_data: Optional[str] = None,
                     ultra_hd: Optional[bool] = None,
                     remember_level: Optional[int] = None,
                     mark_sr_used: Optional[bool] = None) -> Dict[str, str]:
    """写导出面板持久状态 (剪映关闭时写, 重开生效)。"""
    ud = user_data or user_data_dir()
    cfg = os.path.join(ud, "Config")
    written: Dict[str, str] = {}
    if ultra_hd is not None:
        if jianying_running():
            # 剪映在跑时会用内存里的状态回写 Export.ini, 抢不过它
            written["export"] = "SKIPPED: 剪映正在运行, 关闭剪映后再写"
        else:
            written["export"] = _write_ini(
                os.path.join(cfg, EXPORT_INI),
                {"userFirstVideoEnhanceExport": "true" if ultra_hd else "false"})
    if remember_level is not None:
        written["quality_level"] = _write_ini(
            os.path.join(cfg, COMMON_INI),
            {"quality_enhance_last_choose": str(normalize_level(remember_level))})
    if mark_sr_used is not None:
        written["sr_used"] = _write_ini(
            os.path.join(cfg, COMMON_INI),
            {"super_resolution_used": "true" if mark_sr_used else "false"})
    return written


# ---------------------------------------------------------------------------
# 智能判定
# ---------------------------------------------------------------------------
def _h_of(v: Any) -> int:
    s = str(v or "").upper().replace("P", "").replace("K", "0")
    if s in ("8K",):
        return 4320
    if s.endswith("K"):
        try:
            return int(float(s[:-1]) * 1000)
        except ValueError:
            return 0
    for tok in ("4320", "2160", "1440", "1080", "720", "576", "480", "360"):
        if tok in s:
            return int(tok)
    return 0


def short_side(w: Any, h: Any) -> int:
    """素材档位看**短边**: 1920x1080 与 1080x1920 都是 1080P。

    按高度比较会让竖屏素材全部误判成"远超 1080P"。
    """
    try:
        a, b = int(w or 0), int(h or 0)
    except (TypeError, ValueError):
        return 0
    if a <= 0 or b <= 0:
        return max(a, b)
    return min(a, b)


def recommend(target_height: int, source_heights: Sequence[Any],
              upscale_ratio: float = 0.87) -> Dict[str, Any]:
    """按"素材档位是否低于导出档位"判断值不值得开超清。

    target_height / source_* 都是**短边档位** (1080 = 1080P, 2160 = 4K)。

    判定依据 (与官方文案一致):
      pc_export_super_resolution_tell  "素材已达到该分辨率，不需要再处理了"
      pc_exp_ai_uhd_no                 "素材已经足够清晰，暂不需要超清处理"
      pc_quality_4k_enough             "4k的视频已经足够清晰，不需要再做超清处理啦"

    取**最低档**素材当判据: 只要有一路明显低于导出档, 补分辨率就有意义。
    同时要求差距够大 (默认 13% 以上), 否则上传排队的代价换不回可见提升。
    """
    vals = []
    for v in source_heights:
        if isinstance(v, (list, tuple)) and len(v) == 2:
            s = short_side(v[0], v[1])
        else:
            s = int(v or 0)
        if s > 0:
            vals.append(s)

    base = {"target_height": target_height,
            "min_source_height": min(vals) if vals else None,
            "max_source_height": max(vals) if vals else None,
            "est_upload": False, "enable": False}

    if not vals:
        base["reason"] = "无法探测素材分辨率, 不自动开"
        return base
    if target_height <= 0:
        base["reason"] = "导出分辨率未知"
        return base

    lo = min(vals)
    if lo >= 2160:
        base["reason"] = "最低素材已是 4K (pc_quality_4k_enough), 开了没有提升"
        return base
    if lo >= target_height * upscale_ratio:
        base["reason"] = ("全部素材已达到 %dP (最低 %dP), 不需要补"
                          % (target_height, lo))
        return base

    low = sorted({h for h in vals if h < target_height * upscale_ratio})
    base["enable"] = True
    base["est_upload"] = True
    base["reason"] = ("最低素材只有 %dP%s, 低于导出 %dP, 补分辨率有效"
                      % (lo,
                         (" (共 %d 路偏低)" % len(low)) if len(low) > 1 else "",
                         target_height))
    return base


def spec_source_short_sides(spec: Dict[str, Any], probe=_u.probe_media) -> List[int]:
    """按 spec 里的视频素材探测短边档位 (供 recommend 用)。"""
    out: List[int] = []
    for v in spec.get("video") or []:
        p = v.get("path")
        if not p:
            continue
        try:
            path = _u.resolve(spec, p)
            _d, ww, hh = probe(path, (0, 0, 0))
            s = short_side(ww, hh)
            if s:
                out.append(s)
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# 顶层: 一次开关到位
# ---------------------------------------------------------------------------
def apply(draft_dir: str, mode: str = "auto", level: int = 2,
          target_height: int = 1080, only_material_ids: Optional[Sequence[str]] = None,
          dry_run: bool = False, set_draft_flag: bool = False) -> Dict[str, Any]:
    """对一份已有草稿开启/关闭画质能力。

    mode: off | quality_enhance | super_resolution | one_click_ultra_hd | auto

    set_draft_flag: 是否同时把 function_assistant_info.enhance_quality 置真。
        默认 **False**。实测本机真实草稿 (6月7日) 里片段已有
        quality_enhance={'from':'multi_track','level':1}, 而该 draft 级标记
        仍为 False —— 说明权威字段是**片段级** video_algorithm,
        draft 级标记不是生效必需。默认不碰它, 避免写入无依据的状态。
    """
    d = _draft.Draft(draft_dir)
    content = d.read_content()
    result: Dict[str, Any] = {"draft": d.path, "mode": mode, "dry_run": dry_run}
    vids = list(iter_videos(content))
    heights = [int(m.get("height") or 0) for m in vids]
    shorts = [short_side(m.get("width"), m.get("height")) for m in vids]
    rec = recommend(target_height, shorts)
    result["source_heights"] = heights
    result["source_short_sides"] = shorts
    result["recommend"] = rec

    if mode == "auto":
        mode = MODE_SR if rec["enable"] else MODE_OFF
        result["mode"] = mode
        result["auto_reason"] = rec["reason"]

    if mode == MODE_OFF:
        if dry_run:
            return result
        d.backup("quality-off")
        stats = apply_to_segments(content, MODE_OFF)
        for m in iter_videos(content):
            va = m.get("video_algorithm") or {}
            va["quality_enhance"] = None
            va["super_resolution"] = None
        if set_draft_flag:
            update_function_assistant(content, False, [])
        d.write_content(content)
        set_ultra_hd(d.path, False)
        result["stats"] = stats
        result["ultra_hd"] = False
        return result

    if dry_run:
        result["would"] = {"level": normalize_level(level),
                           "segments_mode": mode if mode != MODE_ULTRA else None,
                           "ultra_hd": mode == MODE_ULTRA or rec["enable"]}
        return result

    d.backup("quality-%s" % mode)
    stats = {"quality_enhance": 0, "super_resolution": 0, "segid_list": []}
    if mode in (MODE_QUALITY, MODE_SR):
        stats = apply_to_segments(content, mode, level, only_material_ids)
        if set_draft_flag:
            update_function_assistant(content, True, stats.get("segid_list") or [])
        d.write_content(content)
    ultra = mode == MODE_ULTRA or rec["enable"]
    if ultra:
        set_ultra_hd(d.path, True)
    result["stats"] = stats
    result["ultra_hd"] = ultra
    result["level"] = normalize_level(level) if mode != MODE_ULTRA else None
    try:
        from . import project as _proj
        _proj.register_draft(d.path)
    except Exception:
        pass
    return result


def state(draft_dir: str) -> Dict[str, Any]:
    d = _draft.Draft(draft_dir)
    content = d.read_content()
    segs = read_segment_quality(content)
    fa = content.get("function_assistant_info") or {}
    vids = list(iter_videos(content))
    heights = [int(m.get("height") or 0) for m in vids]
    return {
        "draft": d.path,
        "name": d.name,
        "canvas": content.get("canvas_config"),
        "ultra_hd": read_ultra_hd(d.path),
        "function_assistant": {
            "enhance_quality": fa.get("enhance_quality"),
            "enhance_quality_segid_list": fa.get("enhance_quality_segid_list"),
        },
        "segments": segs,
        "videos": len(vids),
        "source_heights": heights,
        "source_short_sides": [short_side(m.get("width"), m.get("height")) for m in vids],
    }


def plan(draft_dir: str, target_height: Optional[int] = None) -> Dict[str, Any]:
    """值不值得开超清。

    target_height 是**导出分辨率**(导出面板里手选, 默认 1080P),
    不是 canvas_config.height —— 长图/竖屏草稿的画布高度会远大于导出分辨率,
    两者不能混用。
    """
    st = state(draft_dir)
    if target_height is None:
        target_height = infer_target_height(st)
    st["target_height"] = target_height
    st["recommend"] = recommend(target_height, st.get("source_short_sides") or [])
    return st


STANDARD_CANVAS = {
    (3840, 2160): 2160, (2560, 1440): 1440, (1920, 1080): 1080,
    (1280, 720): 720, (854, 480): 480, (640, 360): 360,
    (2160, 3840): 2160, (1440, 2560): 1440, (1080, 1920): 1080,
    (720, 1280): 720, (480, 854): 480, (360, 640): 360,
    (1080, 1080): 1080, (2160, 2160): 2160,
}


def infer_target_height(st: Dict[str, Any], default: int = 1080) -> int:
    """推测导出分辨率 (导出面板手选, 默认 1080P)。

    只看**标准画布尺寸**。长图/漫画/瀑布流草稿的画布高度 (如 1920x3414)
    不是导出分辨率, 一律回退 default —— 否则会把画布高度当导出档,
    得出"素材 720P 低于导出 3414P, 必须超清"这种错误结论。
    """
    cc = st.get("canvas") or {}
    w = int(cc.get("width") or 0)
    h = int(cc.get("height") or 0)
    if not w or not h:
        return default
    if (w, h) in STANDARD_CANVAS:
        return STANDARD_CANVAS[(w, h)]
    ratio = w / float(h)
    if abs(ratio - 16 / 9.0) < 0.02 or abs(ratio - 9 / 16.0) < 0.02:
        for cand in (2160, 1440, 1080, 720):
            if abs(h - cand) <= cand * 0.05 or abs(w - cand) <= cand * 0.05:
                return cand
    return default
