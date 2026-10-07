"""剪映官方素材库下载器 (替代 cloud_manager 的过期静态 CDN).

问题: skill 里 data/cloud_*.csv 存的是 2026-03 签发的 CDN URL, 早就 403.
      add_cloud_media / add_cloud_music 因此全都失败.

本模块做法:
  1. 用 OfficialMaterialAPI 实时向剪映官方接口要数据;
  2. download_info.url 是**当场签发的直链**, 拿到就下, 下完缓存到本地;
  3. 找不到直链时 (图片类素材常为空) 回退 item_urls / cover_url;
  4. 再回退 bridge.downloadOnlineFile (让剪映进程自己下).
  5. 本地 artistEffect 缓存 (剪映自己下过的) 也作为零延迟来源.

对外主入口:
  OfficialAssetManager  .resolve(query) -> AssetRef
                        .download(query) -> local path
  resolve 支持: resource_id / effect_id / 名称模糊 / 关键词搜索
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

from jy_official_api import (
    OfficialMaterialAPI,
    OfficialAPIError,
    EFFECT_TYPE_MATERIAL,
    EFFECT_TYPE_MATERIAL_LIB,
    EFFECT_TYPE_SOUND,
    PANEL_MATERIAL_LIB,
    PANEL_AUDIO,
)

try:
    from utils.config import CONFIG
except Exception:  # 允许独立运行
    class _C:
        cloud_max_mb = 512.0
    CONFIG = _C()  # type: ignore

MAX_DOWNLOAD_BYTES = int(getattr(CONFIG, "cloud_max_mb", 512.0) * 1024 * 1024)

AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def user_cache_root() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    return os.path.join(base, "JianyingPro", "User Data", "Cache")


def _safe_name(text: str, limit: int = 60) -> str:
    cleaned = "".join(c for c in str(text) if c.isalnum() or c in (" ", "_", "-", "."))
    cleaned = re.sub(r"\s+", " ", cleaned).strip().strip(".")
    return (cleaned or "asset")[:limit]


def _ext_from_url(url: str) -> str:
    try:
        path = urlparse(url).path
        ext = os.path.splitext(path)[1].lower()
        if ext in AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS:
            return ext
    except Exception:
        pass
    return ""


def _ext_from_mime(mime: str) -> str:
    mime = (mime or "").lower()
    if "audio" in mime:
        if "mpeg" in mime or "mp3" in mime:
            return ".mp3"
        if "wav" in mime:
            return ".wav"
        if "ogg" in mime:
            return ".ogg"
        return ".m4a"
    if "image" in mime:
        if "png" in mime:
            return ".png"
        if "webp" in mime:
            return ".webp"
        if "gif" in mime:
            return ".gif"
        return ".jpg"
    return ".mp4"


@dataclass
class AssetRef:
    resource_id: str
    title: str = ""
    effect_type: Optional[int] = None
    source: Optional[int] = None
    kind: str = "video"           # video / image / audio
    download_url: str = ""
    preview_url: str = ""
    cover_url: str = ""
    duration_ms: int = 0
    local_path: str = ""
    origin: str = ""              # api / local_cache / bridge / csv
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_s(self) -> float:
        return (self.duration_ms or 0) / 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resource_id": self.resource_id, "title": self.title,
            "effect_type": self.effect_type, "source": self.source,
            "kind": self.kind, "download_url": self.download_url,
            "duration_s": self.duration_s, "local_path": self.local_path,
            "origin": self.origin,
        }


def classify_kind(item: dict) -> str:
    """按 effect_type / 字段判断素材种类."""
    et = item.get("effect_type")
    if et in (EFFECT_TYPE_SOUND, 3, 4, 11):
        return "audio"
    if et in (10, 12):
        return "audio"
    url = (item.get("download_url") or "") + (item.get("detail_url") or "")
    if "audio" in url or "mime_type=audio" in url:
        return "audio"
    ext = _ext_from_url(url)
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in IMAGE_EXTS:
        return "image"
    if item.get("download_format") in ("png", "jpg", "jpeg", "webp", "gif"):
        return "image"
    return "video"


class OfficialAssetManager:
    """官方素材库解析 + 下载 + 本地缓存."""

    def __init__(self, api: Optional[OfficialMaterialAPI] = None,
                 cache_dir: Optional[str] = None,
                 allow_network: bool = True):
        self.api = api or OfficialMaterialAPI()
        self.allow_network = allow_network
        if cache_dir is None:
            base = os.environ.get("JY_CLOUD_CACHE_DIR")
            if not base:
                base = os.path.join(os.path.expanduser("~"), ".jianying_skill_cache")
            cache_dir = base
        self.cache_dir = cache_dir
        os.makedirs(self.cache_dir, exist_ok=True)
        self._index: Optional[List[dict]] = None

    # -- 本地 artistEffect 缓存 --------------------------------------------
    def local_artist_effect_path(self, resource_id: str) -> str:
        """剪映自己下过的素材, 落在 Cache/artistEffect/<rid>/<md5>."""
        rid = str(resource_id)
        for sub in ("artistEffect", "effect"):
            d = os.path.join(user_cache_root(), sub, rid)
            if os.path.isdir(d):
                files = [f for f in os.listdir(d) if os.path.isfile(os.path.join(d, f))]
                if files:
                    # 优先 32 位 hex 文件名 (剪映命名规则)
                    for f in files:
                        if re.fullmatch(r"[a-fA-F0-9]{32}", f):
                            return os.path.join(d, f)
                    return os.path.join(d, files[0])
        return ""

    # -- 解析 --------------------------------------------------------------
    def resolve(self, query: str, *, search: bool = True,
                prefer_kind: Optional[str] = None) -> Optional[AssetRef]:
        """把 query 解析成一个可下载的 AssetRef.

        顺序: resource_id 直查 -> 本地缓存 -> 搜索 -> 名称模糊
        """
        q = str(query).strip()
        if not q:
            return None

        # 1. 纯数字 => 当 resource_id 处理, 先看本地缓存 (零网络)
        if q.isdigit():
            local = self.local_artist_effect_path(q)
            if local:
                return AssetRef(resource_id=q, title=os.path.basename(local),
                                kind="video", local_path=local, origin="local_cache")
            if self.allow_network:
                try:
                    it = self.api.item_detail(q, effect_type=EFFECT_TYPE_MATERIAL)
                    if it:
                        ref = self._from_api_item(it)
                        if ref:
                            return ref
                except Exception:
                    pass

        if not (search and self.allow_network):
            return None

        # 2. 搜索 (素材库 + 音效/音乐)
        for et, panel in ((EFFECT_TYPE_MATERIAL_LIB, PANEL_MATERIAL_LIB),
                          (EFFECT_TYPE_SOUND, PANEL_AUDIO),
                          (4, PANEL_AUDIO)):
            try:
                data = self.api.search(q, count=20, effect_type=et)
            except Exception:
                continue
            items = self.api.iter_normalized(data.get("effect_item_list") or [])
            if not items:
                continue
            if prefer_kind:
                for it in items:
                    if classify_kind(it) == prefer_kind:
                        return self._from_normalized(it)
            return self._from_normalized(items[0])
        return None

    def _from_normalized(self, item: dict) -> AssetRef:
        kind = classify_kind(item)
        return AssetRef(
            resource_id=item.get("resource_id") or "",
            title=item.get("title") or "",
            effect_type=item.get("effect_type"),
            source=item.get("source"),
            kind=kind,
            download_url=item.get("download_url") or "",
            preview_url=item.get("detail_url") or "",
            cover_url=item.get("cover_url") or "",
            duration_ms=0,
            raw=item,
        )

    def _from_api_item(self, item: dict) -> Optional[AssetRef]:
        ca = item.get("common_attr") or {}
        if not ca:
            return None
        dl = ca.get("download_info") or {}
        item_urls = ca.get("item_urls") or []
        cover = ca.get("cover_url") or {}
        norm = {
            "resource_id": str(ca.get("id") or ""),
            "effect_type": ca.get("effect_type"),
            "source": ca.get("source"),
            "title": ca.get("title") or "",
            "download_url": dl.get("url") or "",
            "download_format": dl.get("format") or "",
            "detail_url": item_urls[0] if item_urls else "",
            "cover_url": cover.get("small") or cover.get("static_img") or "",
            "duration": ca.get("duration") or 0,
        }
        ref = self._from_normalized(norm)
        ref.raw = item
        return ref

    # -- 下载 --------------------------------------------------------------
    def download(self, query: str, *, force: bool = False,
                 search: bool = True) -> Optional[str]:
        """解析并下载素材, 返回本地路径; 失败返回 None."""
        ref = self.resolve(query, search=search)
        if ref is None:
            return None
        if ref.local_path and os.path.exists(ref.local_path) and not force:
            return ref.local_path
        return self.download_ref(ref, force=force)

    def download_ref(self, ref: AssetRef, *, force: bool = False) -> Optional[str]:
        dest = os.path.join(
            self.cache_dir,
            "%s_%s" % (ref.resource_id or "asset", _safe_name(ref.title)),
        )

        # 已缓存
        if not force:
            for ext in AUDIO_EXTS | VIDEO_EXTS | IMAGE_EXTS:
                cand = dest + ext
                if os.path.exists(cand) and os.path.getsize(cand) > 0:
                    ref.local_path = cand
                    return cand

        # 候选 URL: download_info -> detail -> cover
        urls: List[str] = []
        for u in (ref.download_url, ref.preview_url, ref.cover_url):
            if u and u not in urls:
                urls.append(u)

        # 网络还没拿到直链时, 重新查一次 (拿到当场签名的)
        if self.allow_network and not ref.download_url:
            try:
                if ref.resource_id.isdigit():
                    it = self.api.item_detail(
                        ref.resource_id, effect_type=ref.effect_type or EFFECT_TYPE_MATERIAL,
                        source=ref.source if ref.source is not None else 1)
                    if it:
                        ca = it.get("common_attr") or {}
                        dl = ca.get("download_info") or {}
                        if dl.get("url"):
                            urls.insert(0, dl["url"])
                        for u in (ca.get("item_urls") or []):
                            if u not in urls:
                                urls.append(u)
            except Exception:
                pass

        for url in urls:
            path = self._fetch_to(url, dest, ref.kind)
            if path:
                ref.local_path = path
                return path

        # 最后回退: 让剪映进程自己下
        path = self._download_via_bridge(ref, dest)
        if path:
            ref.local_path = path
            return path
        return None

    def _fetch_to(self, url: str, dest_no_ext: str, kind: str) -> Optional[str]:
        if not url.lower().startswith(("http://", "https://")):
            return None
        try:
            r = requests.get(url, stream=True, timeout=60,
                             headers={"User-Agent": "JianyingPro/11.5.0 (Windows)"})
            r.raise_for_status()
            ext = _ext_from_url(url) or _ext_from_mime(r.headers.get("Content-Type") or "")
            if not ext:
                ext = {".mp3": "", "audio": ".mp3", "image": ".png"}.get(kind, ".mp4") or ".mp4"
            if kind == "audio" and ext not in AUDIO_EXTS:
                ext = ".mp4" if "mp4" in (r.headers.get("Content-Type") or "") else ".mp3"
            dest = dest_no_ext + ext
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" in ctype:  # 403 页面
                return None
            total = 0
            tmp = dest + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(32768):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError("exceeds size limit")
                    f.write(chunk)
            os.replace(tmp, dest)
            return dest
        except Exception:
            part = dest_no_ext + ".part"
            if os.path.exists(part):
                try:
                    os.remove(part)
                except Exception:
                    pass
            return None

    def _download_via_bridge(self, ref: AssetRef, dest_no_ext: str) -> Optional[str]:
        """让剪映进程下载 (native bridge downloadOnlineFile)."""
        try:
            from jyai import bridge as _bridge
            b = _bridge.Bridge(timeout=30)
            sid = b.open_session()
            url = ref.download_url or ref.preview_url
            if not url:
                return None
            r = b.jsb_raw(sid, "downloadOnlineFile", {
                "url": url, "url_list": [url],
                "file_name": os.path.basename(dest_no_ext),
                "resolve_dir": self.cache_dir,
            }, timeout=30)
            data = r.get("data") if isinstance(r, dict) else None
            if isinstance(data, dict):
                p = data.get("path") or data.get("local_path") or data.get("file_path")
                if p and os.path.exists(p):
                    return p
        except Exception:
            pass
        return None


def make_manager(**kwargs) -> OfficialAssetManager:
    return OfficialAssetManager(**kwargs)