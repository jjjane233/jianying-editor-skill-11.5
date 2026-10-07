"""辅助: 路径解析与媒体探测."""
from __future__ import annotations
import json, os, subprocess, shutil
from typing import Any, Dict, Optional, Tuple


def resolve(spec: Dict[str, Any], path: str) -> str:
    """相对路径按 spec['material_dir'] 解析, 绝对路径原样返回."""
    path = os.path.expandvars(os.path.expanduser(path))
    if os.path.isabs(path):
        return path
    base = spec.get("material_dir") or os.getcwd()
    return os.path.abspath(os.path.join(base, path))


def _ffprobe(path: str) -> Optional[Dict[str, Any]]:
    exe = shutil.which("ffprobe")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "-v", "quiet", "-print_format", "json",
                              "-show_format", "-show_streams", path],
                             capture_output=True, timeout=30)
        if out.returncode == 0 and out.stdout:
            return json.loads(out.stdout.decode("utf-8", "replace"))
    except Exception:
        pass
    return None


def _pymediainfo(path: str) -> Optional[Tuple[int, int, int, bool]]:
    try:
        import pymediainfo
    except ImportError:
        return None
    try:
        info = pymediainfo.MediaInfo.parse(path)
    except Exception:
        return None
    dur_us = 0
    for t in list(info.video_tracks) + list(info.audio_tracks) + list(info.general_tracks):
        d = getattr(t, "duration", None)
        if d:
            dur_us = max(dur_us, int(float(d) * 1000))
    w = h = 0
    if info.video_tracks:
        v = info.video_tracks[0]
        w = int(getattr(v, "width", 0) or 0)
        h = int(getattr(v, "height", 0) or 0)
        return dur_us, w, h, True
    if info.audio_tracks:
        return dur_us, 0, 0, False
    return dur_us, 0, 0, bool(info.image_tracks)


def probe_media(path: str, default: Tuple[int, int, int] = (0, 1920, 1080)) -> Tuple[int, int, int]:
    """返回 (duration_us, width, height). 探测失败时回退 default."""
    r = _pymediainfo(path)
    if r:
        return r[0] or default[0], r[1] or default[1], r[2] or default[2]
    j = _ffprobe(path)
    if j:
        dur = int(float(j.get("format", {}).get("duration", 0) or 0) * 1_000_000)
        w = h = 0
        for s in j.get("streams", []):
            if s.get("codec_type") == "video":
                w = int(s.get("width", 0)); h = int(s.get("height", 0)); break
        return dur or default[0], w or default[1], h or default[2]
    return default


def probe_audio(path: str, default: int = 0) -> Tuple[int, int, int]:
    dur, w, h = probe_media(path, (default, 0, 0))
    return dur or default, w, h
