"""剪映草稿构造引擎: 把一份"剪辑意图 JSON"变成可被剪映打开的草稿.

两种模式:
    build_from_scratch(edit_spec, out_dir)
        依据实测 schema 从零拼装 draft_content.json (明文), 适合全新工程.
        参考 schema 取自 11.3.0.14362 的真实草稿, 见 assets/full_draft_reference.json

    patch_existing(draft, edits)
        在已有草稿上做增量剪辑 (增/删/裁剪/改文本), 保留原有素材引用.

剪辑意图 JSON (edit_spec) 结构:
{
  "name": "我的第一条AI剪辑",
  "width": 1920, "height": 1080, "fps": 30,
  "video": [ {"path": "C:/a.mp4", "start": 0, "duration": 5.0, "source_start": 1.0} ],
  "audio": [ {"path": "C:/b.mp3", "start": 0, "duration": 10.0, "volume": 0.8} ],
  "text":  [ {"text": "标题", "start": 0, "duration": 3.0, "size": 8,
              "x": 0.0, "y": -0.6, "color": "#FFFFFF"} ],
  "material_dir": "C:/materials"      # 相对路径的基准目录(可选)
}
时间单位: 秒 (浮点). 写盘时统一转微秒.
"""
from __future__ import annotations
import json, os, shutil, time, uuid
from typing import Any, Dict, List, Optional

try:
    from . import draft as _draft
    from . import util_jy as _u
except ImportError:  # 单文件运行
    import draft as _draft
    import util_jy as _u

US = 1_000_000

# 本机剪映版本占位 (定义见 detect_jy_version 之后)


def detect_jy_version() -> str:
    """探测本机剪映真实版本号 (Apps\\<ver> 目录名优先, 其次 exe 文件版本)."""
    import glob, re as _re
    base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "JianyingPro", "Apps")
    cands = []
    try:
        for d in glob.glob(os.path.join(base, "*")):
            m = _re.match(r"^\d+(\.\d+){1,3}", os.path.basename(d))
            if m:
                cands.append(m.group(0))
    except Exception:
        pass
    if cands:
        def key(s):
            return tuple(int(x) for x in s.split("."))
        return max(cands, key=key)
    return "11.5.0"


_JY_VER = detect_jy_version()


def resolve_cloud_material(item: Dict[str, Any], cache_dir: Optional[str] = None) -> Optional[str]:
    """把 spec 里的云素材引用解析成本地文件路径.

    支持字段 (任选其一):
      cloud_id     : 官方素材 resource_id (纯数字) —— 精确命中
      cloud_query  : 关键词, 走官方素材库搜索 (视频/音效/音乐)
      cloud_kind   : 可选 video/image/audio, 用于搜索时挑类型

    实现: 复用 scripts/official_asset_manager.OfficialAssetManager,
    它走剪映官方接口拿**当场签发**的 CDN 直链, 避免静态 URL 过期 403.
    解析失败返回 None (调用方决定是跳过还是报错).
    """
    qid = item.get("cloud_id")
    qtext = item.get("cloud_query")
    if not qid and not qtext:
        return None
    query = str(qid) if qid else str(qtext)
    try:
        import os as _os2
        import sys as _sys2
        scripts_dir = _os2.path.dirname(_os2.path.dirname(_os2.path.abspath(__file__)))
        if scripts_dir not in _sys2.path:
            _sys2.path.insert(0, scripts_dir)
        from official_asset_manager import OfficialAssetManager
        mgr = OfficialAssetManager(cache_dir=cache_dir) if cache_dir else OfficialAssetManager()
        return mgr.download(query, search=bool(qtext))
    except Exception as e:
        print("[warn] cloud material resolve failed for %r: %s" % (query, e))
        return None


def _platform_info() -> Dict[str, Any]:
    """剪映写入的 platform / last_modified_platform 结构 (字段与真实草稿一致)."""
    import hashlib, uuid
    node = "%s|%s" % (os.environ.get("COMPUTERNAME", ""), os.environ.get("USERNAME", ""))
    dev = hashlib.md5(node.encode()).hexdigest()
    return {"app_id": 3704, "app_source": "lv", "app_version": _JY_VER,
            "device_id": dev, "hard_disk_id": hashlib.md5((node + "hd").encode()).hexdigest(),
            "mac_address": hashlib.md5((node + "mac").encode()).hexdigest(),
            "os": "windows", "os_version": "10.0.19045"}


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _sec(x: float) -> int:
    return int(round(float(x) * US))


# --------------------------------------------------------------------------
# 素材块
# --------------------------------------------------------------------------
def mk_video_material(path: str, duration_us: int, width: int, height: int,
                      material_type: str = "video") -> Dict[str, Any]:
    mid = _uid()
    return {
        "id": mid, "material_id": mid, "local_material_id": mid,
        "type": material_type, "path": path.replace("\\", "/"),
        "material_name": os.path.basename(path),
        "category_name": "local", "category_id": "",
        "duration": duration_us, "width": width, "height": height,
        "crop": {"upper_left_x": 0.0, "upper_left_y": 0.0,
                 "upper_right_x": 1.0, "upper_right_y": 0.0,
                 "lower_left_x": 0.0, "lower_left_y": 1.0,
                 "lower_right_x": 1.0, "lower_right_y": 1.0},
        "crop_ratio": "free", "crop_scale": 1.0,
        "audio_fade": None, "check_flag": 63487,
        "has_audio": True, "extra_type_option": 0,
        "source_platform": 0, "aigc_type": "none",
        "media_path": "", "reverse_path": "", "intensifies_path": "",
    }


def mk_audio_material(path: str, duration_us: int) -> Dict[str, Any]:
    mid = _uid()
    return {
        "id": mid, "material_id": mid, "local_material_id": mid,
        "music_id": mid, "name": os.path.basename(path),
        "path": path.replace("\\", "/"),
        "type": "extract_music", "category_name": "local", "category_id": "",
        "duration": duration_us, "check_flag": 3,
        "copyright_limit_type": "none", "source_platform": 0,
        "effect_id": "", "formula_id": "", "wave_points": [],
        "app_id": 0,
    }


def mk_text_material(text: str, size: int = 8) -> Dict[str, Any]:
    mid = _uid()
    content = {
        "text": text,
        "styles": [{
            "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                               "solid": {"alpha": 1.0, "color": [1.0, 1.0, 1.0]}}},
            "font": {"path": "", "id": ""},
            "size": size, "range": [0, len(text)],
            "strokes": [{"alpha": 1.0,
                         "content": {"render_type": "solid",
                                     "solid": {"alpha": 1.0, "color": [0.0, 0.0, 0.0]}},
                         "width": 0.08}],
            "useLetterColor": True,
        }],
    }
    return {
        "id": mid, "material_id": mid, "type": "subtitle",
        "content": json.dumps(content, ensure_ascii=False),
        "font_name": "", "font_size": size,
        "text_color": "#FFFFFF", "border_color": "#000000", "border_width": 0.08,
        "border_alpha": 1.0, "background_color": "#00000000",
        "shadow_color": "", "shadow_alpha": 0.9,
        "letter_spacing": 0, "line_spacing": 0.02,
        "alignment": 1, "text_size": size, "bold_width": 0.0,
        "italic_degree": 0, "underline": False, "underline_offset": 0.0,
        "shape_clip_x": False, "is_rich_text": False,
        "line_max_width": 0.82, "line_feed": 1,
        "words": {"end_time": [], "start_time": [], "text": []},
        "current_words": {"end_time": [], "start_time": [], "text": []},
        "recognize_type": 0, "operation_type": 0,
        "fonts": [], "font_path": "", "font_resource_id": "",
        "font_source_platform": 0, "font_team_id": "",
        "force_apply_line_max_width": False,
        "inner_padding": 0.0, "fixed_width": -1.0, "fixed_height": -1.0,
        "initial_scale": 1.0, "sub_type": 3, "sub_template_id": -1,
        "background_horizontal_offset": 0.0, "background_vertical_offset": 0.0,
        "single_char_bg_color": "", "single_char_bg_width": 0.0,
        "single_char_bg_height": 0.0, "single_char_bg_horizontal_offset": 0.0,
        "autoAdaptCanvasEnabled": True, "is_words_linear": False,
        "is_lyric_effect": False, "lyrics_template": "",
        "multi_language_current": "none", "source_from": "",
        "text_loop_on_path": False, "oneline_cutoff": False,
    }


def mk_speed() -> Dict[str, Any]:
    mid = _uid()
    return {"id": mid, "type": "speed", "curve_speed": None, "mode": 0, "speed": 1.0}


def mk_canvas(width: int, height: int) -> Dict[str, Any]:
    mid = _uid()
    return {"id": mid, "type": "canvas_color", "album_image": "", "blur": 0.0,
            "color": "", "image": "", "image_id": "", "image_name": "",
            "source_platform": 0, "team_id": ""}


def mk_sound_channel_mapping() -> Dict[str, Any]:
    mid = _uid()
    return {"id": mid, "type": "none", "audio_channel_mapping": 0,
            "is_config_open": False}


def mk_vocal_separation() -> Dict[str, Any]:
    mid = _uid()
    return {"id": mid, "type": "vocal_separation", "choice": 0,
            "production_path": "", "removed_sounds": [],
            "time_range": None}


# --------------------------------------------------------------------------
# 片段
# --------------------------------------------------------------------------
def mk_video_segment(material: Dict[str, Any], target_start: int, duration: int,
                     source_start: int = 0, render_index: int = 0,
                     speed=None, canvas=None, scm=None, vs=None) -> Dict[str, Any]:
    sid = _uid()
    refs = [x["id"] for x in (speed, canvas, scm, vs) if x]
    return {
        "id": sid, "material_id": material["id"], "raw_segment_id": "",
        "source": "segmentsourcenormal",
        "target_timerange": {"start": target_start, "duration": duration},
        "source_timerange": {"start": source_start, "duration": duration},
        "render_timerange": {"start": 0, "duration": 0},
        "extra_material_refs": refs,
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                 "rotation": 0.0, "scale": {"x": 1.0, "y": 1.0},
                 "transform": {"x": 0.0, "y": 0.0}},
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True, "volume": 1.0, "last_nonzero_volume": 1.0,
        "speed": 1.0, "reverse": False, "is_loop": False,
        "state": 0, "desc": "", "cartoon": False,
        "group_id": "", "track_attribute": 0, "track_render_index": 0,
        "render_index": render_index,
        "hdr_settings": {"intensity": 1.0, "mode": 1, "nits": 1000},
        "responsive_layout": {"enable": False, "horizontal_layout": False,
                              "horizontal_layout_type": "", "size_layout": 0,
                              "target_follow": ""},
        "enable_adjust": True, "enable_color_curves": True, "enable_hsl": False,
        "enable_hsl_curves": True, "enable_lut": True, "enable_mask_shadow": False,
        "enable_mask_stroke": False, "enable_smart_color_adjust": False,
        "enable_color_match_adjust": False, "enable_color_correct_adjust": False,
        "enable_adjust_mask": False, "enable_color_wheels": True,
        "enable_color_adjust_pro": False,
        "common_keyframes": [], "keyframe_refs": [], "segment_color_tag": "",
        "is_placeholder": False, "template_id": "", "template_scene": "default",
        "digital_human_template_group_id": "", "is_tone_modify": False,
        "intensifies_audio": False, "color_correct_alg_result": "",
    }


def mk_audio_segment(material: Dict[str, Any], target_start: int, duration: int,
                     source_start: int = 0, volume: float = 1.0,
                     render_index: int = 0, speed=None) -> Dict[str, Any]:
    sid = _uid()
    refs = [x["id"] for x in (speed,) if x]
    return {
        "id": sid, "material_id": material["id"],
        "target_timerange": {"start": target_start, "duration": duration},
        "source_timerange": {"start": source_start, "duration": duration},
        "extra_material_refs": refs,
        "volume": volume, "last_nonzero_volume": volume,
        "speed": 1.0, "visible": True, "state": 0,
        "render_index": render_index, "track_render_index": 0,
        "source": "segmentsourcenormal", "is_loop": False, "reverse": False,
        "desc": "", "group_id": "", "track_attribute": 0,
        "intensifies_audio": False, "is_tone_modify": False,
        "common_keyframes": [], "keyframe_refs": [],
    }


def mk_text_segment(material: Dict[str, Any], target_start: int, duration: int,
                    x: float = 0.0, y: float = 0.0, scale: float = 1.0,
                    render_index: int = 0) -> Dict[str, Any]:
    sid = _uid()
    return {
        "id": sid, "material_id": material["id"],
        "target_timerange": {"start": target_start, "duration": duration},
        "source_timerange": None,
        "extra_material_refs": [],
        "clip": {"alpha": 1.0, "flip": {"horizontal": False, "vertical": False},
                 "rotation": 0.0, "scale": {"x": scale, "y": scale},
                 "transform": {"x": x, "y": y}},
        "uniform_scale": {"on": True, "value": 1.0},
        "visible": True, "state": 0, "render_index": render_index,
        "track_render_index": 0, "desc": "", "group_id": "",
        "track_attribute": 0, "source": "segmentsourcenormal",
        "common_keyframes": [], "keyframe_refs": [],
        "enable_adjust": True, "enable_color_curves": True,
        "enable_hsl_curves": True, "enable_lut": True,
        "enable_color_wheels": True, "enable_adjust_mask": False,
        "enable_mask_shadow": False, "enable_mask_stroke": False,
        "enable_smart_color_adjust": False, "enable_color_match_adjust": False,
        "enable_color_correct_adjust": False, "enable_color_adjust_pro": False,
        "is_tone_modify": False, "intensifies_audio": False,
        "is_placeholder": False, "template_id": "", "template_scene": "default",
    }


def mk_track(track_type: str, segments: List[Dict[str, Any]],
             render_index: int = 0, mute: bool = False) -> Dict[str, Any]:
    return {
        "id": _uid(), "type": track_type,
        "attribute": 0, "flag": 0, "is_default_name": True,
        "name": "", "segments": segments,
        "render_index": render_index, "mute": mute,
    }


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------
def build_from_scratch(spec: Dict[str, Any], out_dir: str,
                       media_probe=None, register: bool = True) -> Dict[str, Any]:
    """依据 edit_spec 在 out_dir 生成一个新草稿 (明文 draft_content.json).

    media_probe: 可选, callable(path) -> (duration_us, width, height)
                 默认用 ffprobe / pymediainfo 探测.
    """
    name = spec.get("name") or ("AI_" + time.strftime("%Y%m%d_%H%M%S"))
    width = int(spec.get("width", 1920))
    height = int(spec.get("height", 1080))
    fps = int(spec.get("fps", 30))
    if name != os.path.basename(name) or name in (".", ".."):
        raise ValueError("草稿名称应是单个文件夹名称: " + name)
    draft_dir = os.path.join(out_dir, name)
    if os.path.exists(draft_dir):
        raise FileExistsError("草稿已存在, 使用新名称或 patch 修改: " + draft_dir)
    os.makedirs(draft_dir)

    content = _draft.skeleton()
    content["canvas_config"] = {"width": width, "height": height, "ratio": "original"}
    content["fps"] = float(fps)
    content["id"] = _uid()
    content["name"] = name
    content["draft_type"] = "video"
    # 剪映 11.x 读取时强校验这些字段, 缺失会被判为损坏草稿
    content["version"] = 360000
    content["new_version"] = "125.0.0"
    content["source"] = "default"
    content["color_space"] = 0
    content["platform"] = _platform_info()
    content["last_modified_platform"] = _platform_info()
    content["create_time"] = int(time.time() * 1_000_000)
    content["update_time"] = content["create_time"]
    content["render_index_track_mode_on"] = True

    mats = content["materials"]
    for k in list(mats.keys()):
        mats[k] = []

    tracks: List[Dict[str, Any]] = []

    # ---- 视频轨 ----
    vids = spec.get("video") or []
    if vids:
        vsegs, ri = [], 0
        for i, v in enumerate(vids):
            if v.get("cloud_id") or v.get("cloud_query"):
                cloud_path = resolve_cloud_material(v, cache_dir=spec.get("cloud_cache_dir"))
                if not cloud_path:
                    print("[warn] skip cloud video %r (resolve failed)"
                          % (v.get("cloud_query") or v.get("cloud_id")))
                    continue
                path = cloud_path
            else:
                path = _u.resolve(spec, v["path"])
            dur = _sec(v.get("duration", 5.0))
            src_start = _sec(v.get("source_start", 0.0))
            if media_probe:
                try:
                    mdur, mw, mh = media_probe(path)
                except Exception:
                    mdur, mw, mh = dur, width, height
            else:
                mdur, mw, mh = _u.probe_media(path, default=(dur, width, height))
            m = mk_video_material(path, max(mdur, dur + src_start), mw, mh,
                                  material_type="photo" if v.get("is_image") else "video")
            mats["videos"].append(m)
            sp = mk_speed(); mats["speeds"].append(sp)
            cv = mk_canvas(width, height); mats["canvases"].append(cv)
            sc = mk_sound_channel_mapping(); mats["sound_channel_mappings"].append(sc)
            vs = mk_vocal_separation(); mats["vocal_separations"].append(vs)
            ph = {"id": _uid(), "type": "placeholder_info", "meta_type": "none",
                  "res_path": "", "res_text": "", "error_path": "",
                  "error_text": ""}
            mats["placeholder_infos"].append(ph)
            vsegs.append(mk_video_segment(m, _sec(v.get("start", 0.0)), dur, src_start, ri, sp, cv, sc, vs))
            if spec.get("mute") or v.get("mute"):
                vsegs[-1]["volume"] = 0.0
            ri += 1
        tracks.append(mk_track("video", vsegs, render_index=0))

    # ---- 音频轨 ----
    auds = spec.get("audio") or []
    if auds:
        asegs, ri = [], 0
        for a in auds:
            if a.get("cloud_id") or a.get("cloud_query"):
                cloud_path = resolve_cloud_material(a, cache_dir=spec.get("cloud_cache_dir"))
                if not cloud_path:
                    print("[warn] skip cloud audio %r (resolve failed)"
                          % (a.get("cloud_query") or a.get("cloud_id")))
                    continue
                path = cloud_path
            else:
                path = _u.resolve(spec, a["path"])
            dur = _sec(a.get("duration", 10.0))
            mdur, _w, _h = _u.probe_audio(path, default=dur)
            m = mk_audio_material(path, max(mdur, dur))
            mats["audios"].append(m)
            sp = mk_speed(); mats["speeds"].append(sp)
            asegs.append(mk_audio_segment(m, _sec(a.get("start", 0.0)), dur,
                                          _sec(a.get("source_start", 0.0)),
                                          float(a.get("volume", 1.0)), ri, sp))
            ri += 1
        tracks.append(mk_track("audio", asegs, render_index=len(tracks)))

    # ---- 文本轨 ----
    txts = spec.get("text") or []
    if txts:
        tsegs, ri = [], 0
        for t in txts:
            m = mk_text_material(t.get("text", ""), int(t.get("size", 8)))
            mats["texts"].append(m)
            tsegs.append(mk_text_segment(m, _sec(t.get("start", 0.0)),
                                         _sec(t.get("duration", 3.0)),
                                         float(t.get("x", 0.0)), float(t.get("y", 0.0)),
                                         float(t.get("scale", 1.0)), ri))
            ri += 1
        tracks.append(mk_track("text", tsegs, render_index=len(tracks)))

    content["tracks"] = tracks
    content["duration"] = max(
        [s["target_timerange"]["start"] + s["target_timerange"]["duration"]
         for tr in tracks for s in tr["segments"]] or [0])

    # ---- 写盘 ----
    draft_dir = _write_draft(draft_dir, name, content)

    # ---- 11.x 结构: Timelines/<id>/... + 注册到 root_meta_info ----
    try:
        from . import project as _proj
    except ImportError:
        import project as _proj
    tl = _proj.write_timelines(draft_dir, content)
    from .repair import repair
    repair(draft_dir, register=register, backup=False)
    registered = _proj.read_meta_id(draft_dir) if register else None
    content = _draft.Draft(draft_dir).read_content()

    return {"draft_dir": draft_dir, "name": name, "tracks": len(tracks),
            "draft_id": registered, "timelines": tl,
            "content": content}


def _write_draft(draft_dir: str, name: str, content: Dict[str, Any]) -> str:
    data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    paths = ["draft_content.json", "template-2.tmp", "draft_content.json.bak"]
    for rel in paths:
        with open(os.path.join(draft_dir, rel), "wb") as f:
            f.write(data)

    meta = {
        "draft_id": content["id"], "draft_name": name,
        "draft_fold_path": draft_dir.replace("\\", "/"),
        "draft_root_path": os.path.dirname(draft_dir).replace("\\", "/"),
        "draft_json_file": os.path.join(draft_dir, "draft_content.json").replace("\\", "/"),
        "draft_type": "", "draft_new_version": "",
        "draft_cover": "", "draft_is_invisible": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "", "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "", "draft_cloud_videocut_purchase_info": "",
        "draft_is_ai_shorts": False, "draft_is_cloud_temp_draft": False,
        "draft_is_pippit_draft": False, "draft_is_web_article_video": False,
        "draft_deeplink_url": "", "draft_is_from_deeplink": "false",
        "draft_enterprise_info": {"draft_enterprise_extra": "",
                                  "draft_enterprise_id": "",
                                  "draft_enterprise_name": "",
                                  "enterprise_material": []},
        "draft_materials": [{"type": t, "value": []} for t in (0, 1, 2, 3, 6, 7, 8)],
        "draft_materials_copied_info": [], "draft_removable_storage_device": "",
        "draft_segment_extra_info": [],
        "tm_draft_cloud_completed": "", "tm_draft_cloud_modified": 0,
        "tm_draft_removed": 0, "tm_duration": content.get("duration", 0),
        "draft_cloud_capcut_purchase_info": "",
        "draft_is_ai_packaging_used": False, "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "cloud_package_completed_time": "",
    }
    with open(os.path.join(draft_dir, "draft_meta_info.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=4)

    with open(os.path.join(draft_dir, "draft_virtual_store.json"), "w", encoding="utf-8") as f:
        json.dump({"draft_materials": [],
                   "draft_virtual_store": [{"type": 0, "value": []},
                                           {"type": 1, "value": []},
                                           {"type": 2, "value": []}]},
                  f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(draft_dir, "timeline_layout.json"), "w", encoding="utf-8") as f:
        json.dump({"activeTimeline": "", "dockItems": [], "layoutOrientation": 1},
                  f, ensure_ascii=False, separators=(",", ":"))

    with open(os.path.join(draft_dir, "draft_settings"), "w", encoding="utf-8") as f:
        f.write("[General]\ncloud_last_modify_platform=windows\ndraft_create_time=%d\n"
                "draft_last_edit_time=%d\nreal_edit_seconds=0\nreal_edit_keys=0\n"
                % (int(time.time()), int(time.time())))

    os.makedirs(os.path.join(draft_dir, ".backup"), exist_ok=True)
    return draft_dir


# --------------------------------------------------------------------------
# 增量修改
# --------------------------------------------------------------------------
def patch_existing(draft_path: str, edits: Dict[str, Any]) -> Dict[str, Any]:
    """在已有草稿上做增量编辑, 返回变更摘要.

    edits 支持:
        add_video / add_audio / add_text : 与 build_from_scratch 中同名字段一致
        remove_segments : [segment_id, ...]
        set_volume : {segment_id: 0..1}
        retime : {segment_id: {"start": s, "duration": d}}
        rename : "新名字"
    """
    d = _draft.Draft(draft_path)
    content = d.read_content()
    mats = content.setdefault("materials", {})
    for k in ("videos", "audios", "texts", "speeds", "canvases",
              "sound_channel_mappings", "vocal_separations", "placeholder_infos"):
        mats.setdefault(k, [])
    tracks = content.setdefault("tracks", [])
    changed: Dict[str, Any] = {"added": [], "removed": [], "retimed": [], "volume": []}

    w = int(content.get("canvas_config", {}).get("width", 1920))
    h = int(content.get("canvas_config", {}).get("height", 1080))

    def _track(ttype: str) -> Dict[str, Any]:
        for t in tracks:
            if t.get("type") == ttype:
                return t
        t = mk_track(ttype, [], render_index=len(tracks))
        tracks.append(t)
        return t

    for v in edits.get("add_video", []) or []:
        path = _u.resolve(edits, v["path"])
        dur = _sec(v.get("duration", 5.0)); src = _sec(v.get("source_start", 0.0))
        mdur, mw, mh = _u.probe_media(path, default=(dur, w, h))
        m = mk_video_material(path, max(mdur, dur + src), mw, mh); mats["videos"].append(m)
        sp = mk_speed(); mats["speeds"].append(sp)
        cv = mk_canvas(w, h); mats["canvases"].append(cv)
        sc = mk_sound_channel_mapping(); mats["sound_channel_mappings"].append(sc)
        vs = mk_vocal_separation(); mats["vocal_separations"].append(vs)
        seg = mk_video_segment(m, _sec(v.get("start", 0.0)), dur, src, 0, sp, cv, sc, vs)
        _track("video")["segments"].append(seg)
        changed["added"].append(seg["id"])

    for a in edits.get("add_audio", []) or []:
        path = _u.resolve(edits, a["path"])
        dur = _sec(a.get("duration", 10.0))
        mdur, _w, _h = _u.probe_audio(path, default=dur)
        m = mk_audio_material(path, max(mdur, dur)); mats["audios"].append(m)
        sp = mk_speed(); mats["speeds"].append(sp)
        seg = mk_audio_segment(m, _sec(a.get("start", 0.0)), dur,
                               _sec(a.get("source_start", 0.0)),
                               float(a.get("volume", 1.0)), 0, sp)
        _track("audio")["segments"].append(seg)
        changed["added"].append(seg["id"])

    for t in edits.get("add_text", []) or []:
        m = mk_text_material(t.get("text", ""), int(t.get("size", 8)))
        mats["texts"].append(m)
        seg = mk_text_segment(m, _sec(t.get("start", 0.0)), _sec(t.get("duration", 3.0)),
                              float(t.get("x", 0.0)), float(t.get("y", 0.0)),
                              float(t.get("scale", 1.0)), 0)
        _track("text")["segments"].append(seg)
        changed["added"].append(seg["id"])

    rm = set(edits.get("remove_segments") or [])
    if rm:
        for tr in tracks:
            keep = []
            for s in tr.get("segments", []):
                if s.get("id") in rm:
                    changed["removed"].append(s["id"])
                else:
                    keep.append(s)
            tr["segments"] = keep

    for sid, val in (edits.get("set_volume") or {}).items():
        for tr in tracks:
            for s in tr.get("segments", []):
                if s.get("id") == sid:
                    s["volume"] = float(val); s["last_nonzero_volume"] = float(val)
                    changed["volume"].append(sid)

    for sid, rt in (edits.get("retime") or {}).items():
        for tr in tracks:
            for s in tr.get("segments", []):
                if s.get("id") == sid:
                    if "start" in rt: s["target_timerange"]["start"] = _sec(rt["start"])
                    if "duration" in rt: s["target_timerange"]["duration"] = _sec(rt["duration"])
                    changed["retimed"].append(sid)

    if edits.get("rename"):
        changed["rename"] = edits["rename"]

    content["duration"] = max(
        [s["target_timerange"]["start"] + s["target_timerange"]["duration"]
         for tr in tracks for s in tr.get("segments", [])
         if s.get("target_timerange")] or [0])

    d.backup("patch")
    d.write_content(content)
    # 同步 11.x 的 Timelines 副本与索引列表, 否则剪映仍显示旧时长
    try:
        from . import project as _proj
    except ImportError:
        import project as _proj
    tid = d.active_timeline()
    _proj.write_timelines(d.path, content, timeline_id=tid)
    d.repair(backup=False)
    if edits.get("rename"):
        _proj.unregister(d.path)
        new_path = os.path.join(os.path.dirname(d.path), edits["rename"])
        os.rename(d.path, new_path)
        d.path = new_path
        d.write_meta_name()
        d.repair(backup=False)
    return changed



