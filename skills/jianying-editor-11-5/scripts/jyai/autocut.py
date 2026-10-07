"""一句话剪视频 -- 把自然语言需求变成剪映草稿.

这是"AI 直接剪辑"的最短可用链路 (不需要登录, 不需要 app-server):

    素材 + 一句话  ->  jyai.autocut  ->  剪映草稿 (11.5 原生加密内容)
                                          -> 剪映里直接打开就能看/导

设计
----
1. `plan()`  把一句话需求解析成结构化的 `Plan` (规格 + 剪辑动作)
2. `render()` 调 engine.build_from_scratch 落成草稿
3. `autocut()` = plan + render, 一步到位

支持的指令关键词 (中英)
--------------------
    竖屏/横屏/方形        aspect     1080x1920 / 1920x1080 / 1080x1080
    N秒 / Ns             duration   总时长
    卡点/节奏            beat       按素材切分
    字幕/标题            text       加文字
    配音/静音/配乐        audio      音频处理
    淡入淡出             fade
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from . import util_jy as _U
from . import engine as _E

__all__ = ["parse_prompt", "build_spec", "render", "autocut", "list_media",
           "SUPPORTED_VIDEO", "SUPPORTED_AUDIO", "SUPPORTED_IMAGE"]

SUPPORTED_VIDEO = (".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".m4v", ".webm")
SUPPORTED_AUDIO = (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg")
SUPPORTED_IMAGE = (".jpg", ".jpeg", ".png", ".bmp", ".webp")

ASPECTS = {
    "竖屏": (1080, 1920), "竖": (1080, 1920), "vertical": (1080, 1920),
    "9:16": (1080, 1920), "抖": (1080, 1920), "tiktok": (1080, 1920),
    "横屏": (1920, 1080), "横": (1920, 1080), "horizontal": (1920, 1080),
    "16:9": (1920, 1080), "宽屏": (1920, 1080),
    "方形": (1080, 1080), "正方": (1080, 1080), "square": (1080, 1080),
    "1:1": (1080, 1080),
}


def list_media(folder: str, recursive: bool = False) -> List[str]:
    """列出目录里的可用素材 (视频/音频/图片), 按文件名排序."""
    out: List[str] = []
    exts = SUPPORTED_VIDEO + SUPPORTED_AUDIO + SUPPORTED_IMAGE
    if recursive:
        for base, _dirs, files in os.walk(folder):
            for f in files:
                if f.lower().endswith(exts):
                    out.append(os.path.join(base, f))
    else:
        for f in os.listdir(folder):
            p = os.path.join(folder, f)
            if os.path.isfile(p) and f.lower().endswith(exts):
                out.append(p)
    return sorted(out)


def parse_prompt(prompt: str) -> Dict[str, Any]:
    """把一句话需求解析成参数字典.

    只识别明确的模式, 识别不到就用默认值, **不会因为解析不出就报错**.
    """
    p = prompt or ""
    low = p.lower()
    out: Dict[str, Any] = {}

    # --- 画幅 ---
    for kw, (w, h) in ASPECTS.items():
        if kw in p or kw in low:
            out["width"], out["height"] = w, h
            break
    out.setdefault("width", 1080)
    out.setdefault("height", 1920)

    # --- 每段时长: "每段5秒" "3秒" "5s" "5 秒" ---
    m = re.search(r"(?:每[段个条]\s*)?(\d+(?:\.\d+)?)\s*(?:秒|s\b|sec)", low)
    if m:
        out["per_clip"] = float(m.group(1))

    # --- 总时长: "总共30秒" "时长20秒" ---
    m = re.search(r"(?:总共|共计|时长|总长|一共)\s*(\d+(?:\.\d+)?)\s*(?:秒|s\b|sec)", low)
    if m:
        out["total_duration"] = float(m.group(1))

    # --- 文字 ---
    texts = re.findall(r"[\"“']([^\"”']{1,40})[\"”']", p)
    if texts:
        out["texts"] = texts
    out["want_text"] = bool(re.search(r"字幕|标题|文字|文案|text|title|subtitle", low))

    # --- 音频 ---
    out["mute"] = bool(re.search(r"静音|mute|去掉声音|无声", low))
    out["want_bgm"] = bool(re.search(r"配乐|bgm|背景音乐|音乐", low))

    # --- 节奏 ---
    out["beat"] = bool(re.search(r"卡点|节奏|踩点|beat|快剪", low))

    # --- 画质 / 帧率 ---
    m = re.search(r"(\d+)\s*(?:fps|帧)", low)
    if m:
        try:
            out["fps"] = int(m.group(1))
        except ValueError:
            pass
    out.setdefault("fps", 30)

    out["raw"] = prompt
    return out


def build_spec(media: List[str], prompt: str = "", name: Optional[str] = None,
               per_clip: Optional[float] = None, total: Optional[float] = None,
               probe= None) -> Dict[str, Any]:
    """把素材 + 一句话 变成 engine 能吃的 spec.

    :param media: 素材绝对路径列表, 按顺序排入时间线
    :param per_clip: 每个片段截取秒数 (默认 5)
    :param total: 总时长上限 (秒), 超出的尾部素材被裁掉
    """
    pr = parse_prompt(prompt or "")
    per = per_clip if per_clip is not None else pr.get("per_clip", 5.0)
    cap = total if total is not None else pr.get("total_duration")
    probe = probe or _U.probe_media

    videos, audios, images = [], [], []
    for m in media:
        ext = os.path.splitext(m)[1].lower()
        if ext in SUPPORTED_VIDEO:
            videos.append(m)
        elif ext in SUPPORTED_AUDIO:
            audios.append(m)
        elif ext in SUPPORTED_IMAGE:
            images.append(m)

    video_specs: List[Dict[str, Any]] = []
    cursor = 0.0
    for v in videos:
        dur_us, _w, _h = probe(v, (5_000_000, 1920, 1080))
        avail = dur_us / 1_000_000.0
        take = min(per, avail) if avail > 0 else per
        if cap is not None and cursor >= cap:
            break
        if cap is not None:
            take = min(take, cap - cursor)
        if take <= 0.05:
            break
        video_specs.append({
            "path": v,
            "start": round(cursor, 3),
            "duration": round(take, 3),
            "source_start": 0.0,
        })
        cursor += take

    for img in images:
        if cap is not None and cursor >= cap:
            break
        take = per
        if cap is not None:
            take = min(take, cap - cursor)
        if take <= 0.05:
            break
        video_specs.append({
            "path": img,
            "start": round(cursor, 3),
            "duration": round(take, 3),
            "source_start": 0.0,
            "is_image": True,
        })
        cursor += take

    text_specs: List[Dict[str, Any]] = []
    if pr.get("want_text") or pr.get("texts"):
        strings = pr.get("texts") or [os.path.splitext(os.path.basename(media[0]))[0]
                                      if media else "我的视频"]
        seg = max(2.0, (cursor / max(1, len(strings))) if cursor else 3.0)
        t = 0.0
        for s in strings:
            text_specs.append({
                "text": s, "start": round(t, 3),
                "duration": round(min(seg, max(1.0, cursor - t)), 3),
                "size": 9, "x": 0.0, "y": -0.55,
            })
            t += seg

    audio_specs: List[Dict[str, Any]] = []
    if audios and not pr.get("mute"):
        audio_specs.append({
            "path": audios[0], "start": 0.0, "duration": round(cursor, 3),
            "source_start": 0.0, "volume": 0.6,
        })

    spec: Dict[str, Any] = {
        "name": name or ("AI_" + time.strftime("%m%d_%H%M%S")),
        "width": pr["width"], "height": pr["height"], "fps": pr["fps"],
        "video": video_specs, "audio": audio_specs, "text": text_specs,
    }
    if pr.get("mute"):
        spec["mute"] = True
    return spec


def render(spec: Dict[str, Any], out_dir: str, register: bool = True) -> Dict[str, Any]:
    """把 spec 落成剪映草稿, 返回结果摘要."""
    return _E.build_from_scratch(spec, out_dir, register=register)


def autocut(media_folder: str, prompt: str = "", out_dir: Optional[str] = None,
            name: Optional[str] = None, per_clip: Optional[float] = None,
            total: Optional[float] = None, register: bool = True,
            recursive: bool = False, limit: int = 20,
            quality: str = "auto", level: int = 2,
            target_height: Optional[int] = None) -> Dict[str, Any]:
    """★ 一句话剪视频: 给素材目录 + 一句话, 直接产出剪映草稿.

    :param media_folder: 素材所在目录
    :param prompt: 一句话需求, 例如 "竖屏 每段3秒 总共15秒 加标题"
    :param out_dir: 草稿根目录；默认从 JY_DRAFTS_ROOT 或本机首页索引探测
    :param limit: 最多用几个素材
    :param quality: 画质策略。
        "off"                不动画质
        "auto" (默认)        按素材档位判定, 值得才开 (不给 1080P 素材开 1080P 超清)
        "quality_enhance"    强制本地超清画质 (便宜, 不排队)
        "super_resolution"   强制云端补分辨率 (会排队)
        "one_click_ultra_hd" 只开导出面板的一键超清
    :param target_height: 导出目标档位 (短边), 默认按草稿推断
    """
    media = list_media(media_folder, recursive=recursive)[:limit]
    if not media:
        raise FileNotFoundError("目录里没有可用素材: %s" % media_folder)
    spec = build_spec(media, prompt, name=name, per_clip=per_clip, total=total)
    if out_dir is None:
        out_dir = _default_draft_root()
    res = render(spec, out_dir, register=register)
    res["spec"] = spec
    res["media_used"] = media

    if quality and quality != "off":
        try:
            from . import quality as _Q
            draft_dir = res.get("draft_dir") or res.get("path")
            if draft_dir and os.path.isdir(draft_dir):
                if target_height is None:
                    target_height = _Q.infer_target_height(
                        {"canvas": {"width": spec.get("width"),
                                    "height": spec.get("height")}})
                res["quality"] = _Q.apply(draft_dir, mode=quality, level=level,
                                          target_height=int(target_height))
        except Exception as exc:      # 画质是增强项, 不能影响主剪辑链路
            res["quality_error"] = "%s: %s" % (type(exc).__name__, exc)
    return res


def _canon(path: str) -> str:
    """绝对路径 + 归一化（把 8.3 短路径等价形式规范成实际路径）。"""
    return os.path.realpath(os.path.expandvars(os.path.expanduser(path)))


def _default_draft_root() -> str:
    configured = os.environ.get('JY_DRAFTS_ROOT')
    if configured:
        return _canon(configured)
    from . import project
    meta = project.load_root_meta()
    for entry in meta.get('all_draft_store', []):
        root = entry.get('draft_root_path')
        if root and os.path.isdir(root):
            return _canon(root)
        folder = entry.get('draft_fold_path')
        if folder and os.path.isdir(folder):
            return os.path.dirname(_canon(folder))
    for c in (r"D:\Jianying\Data1\JianyingPro Drafts",
              os.path.expanduser(r"~\Documents\JianyingPro Drafts")):
        if os.path.isdir(c):
            return c
    raise FileNotFoundError("找不到剪映草稿目录, 请显式传 out_dir")
