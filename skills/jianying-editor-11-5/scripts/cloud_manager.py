import argparse
import csv
import ipaddress
import os
import re
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests
from utils.config import CONFIG
from utils.logging_utils import setup_logger

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(SKILL_ROOT)))
CACHE_DIR = os.path.join(WORKSPACE_ROOT, "cloud_cache")
MAX_DOWNLOAD_BYTES = int(CONFIG.cloud_max_mb * 1024 * 1024)
ALLOWED_SCHEMES = {"http", "https"}
logger = setup_logger("cloud_manager")


class CloudManager:
    def __init__(self):
        self.assets = self._load_database()
        self._official = None
        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)

    def _load_database(self) -> Dict[str, dict]:
        assets: Dict[str, dict] = {}
        db_files = ["cloud_music_library.csv", "cloud_video_assets.csv", "cloud_sound_effects.csv"]

        for db_name in db_files:
            path = os.path.join(SKILL_ROOT, "data", db_name)
            if not os.path.exists(path):
                continue

            try:
                with open(path, "r", encoding="utf-8") as f:
                    lines = [line for line in f.readlines() if not line.startswith("#")]
                    reader = csv.DictReader(lines)
                    for row in reader:
                        eid = row.get("id") or row.get("music_id") or row.get("effect_id")
                        if not eid:
                            continue
                        name = row.get("name") or row.get("title") or row.get("name_hint") or ""
                        dur = row.get("duration_s") or row.get("duration")
                        assets[str(eid)] = {
                            "id": str(eid),
                            "name": str(name),
                            "url": row.get("url", ""),
                            "duration_s": (
                                float(dur)
                                if dur and str(dur).replace(".", "", 1).isdigit()
                                else None
                            ),
                            "type": row.get("type") or row.get("categories", "unknown"),
                            "source_db": db_name,
                        }
            except Exception as e:
                logger.warning("Error loading %s: %s", db_name, e)

        if assets:
            logger.info("Cloud Manager indexed %d items.", len(assets))
        return assets

    @property
    def official(self):
        """官方素材库管理器 (实时签名直链), 首次访问才构造."""
        if self._official is None:
            try:
                from official_asset_manager import OfficialAssetManager
                self._official = OfficialAssetManager(cache_dir=CACHE_DIR)
            except Exception as e:
                logger.warning("Official asset manager unavailable: %s", e)
                self._official = False
        return self._official or None

    def find_asset(self, query: str) -> Optional[dict]:
        """
        Find by ID or fuzzy name.
        11.5: 素材下载地址改为运行时动态签发, 因此索引行不再要求带 url
        (旧的 "无 url 即不可用" 规则会让动态通道永远命中不到).
        """
        if query in self.assets:
            return self.assets[query]

        q = str(query).lower()
        for asset in self.assets.values():
            if q and q in str(asset.get("name", "")).lower():
                return asset
        return None

    def get_asset_duration(self, query: str) -> Optional[float]:
        asset = self.find_asset(query)
        if asset:
            return asset.get("duration_s")
        return None

    def get_url_from_logs(self, effect_id: str) -> Optional[str]:
        log_files = [
            os.path.join(WORKSPACE_ROOT, "mitmdump_assets_capture.log"),
            os.path.join(WORKSPACE_ROOT, "mitmdump_media_full.log"),
            r"d:\jianying\网页剪辑\mitmdump_assets_capture.log",
        ]

        for log_path in log_files:
            if not os.path.exists(log_path):
                continue
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            id_pattern = f'"(?:effect_id|id)":"{effect_id}"'
            matches = list(re.finditer(id_pattern, content))
            if not matches:
                continue

            for m in reversed(matches):
                region = content[m.end() : m.end() + 10000]
                url_match = re.search(
                    r'https?://[^\s"\'\]]+(?:\.mp4|\.webm|\.zip|\.7z|a=4066)[^\s"\'\]]*',
                    region,
                    re.IGNORECASE,
                )
                if url_match:
                    return url_match.group(0).replace("\\u0026", "&").replace("\\/", "/")
        return None

    def _is_safe_download_url(self, url: str) -> bool:
        try:
            parsed = urlparse(url)
            if parsed.scheme.lower() not in ALLOWED_SCHEMES:
                return False
            host = (parsed.hostname or "").strip().lower()
            if not host:
                return False
            if host in {"localhost", "127.0.0.1", "::1"}:
                return False
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return False
            except ValueError:
                pass
            return True
        except Exception:
            return False

    def _validate_response_headers(self, res: requests.Response) -> bool:
        content_type = (res.headers.get("Content-Type") or "").lower()
        if content_type.startswith("text/html") or content_type.startswith("application/json"):
            return False
        if content_type and not (
            "video/" in content_type
            or "audio/" in content_type
            or "application/octet-stream" in content_type
            or "application/zip" in content_type
            or "binary/octet-stream" in content_type
        ):
            return False
        content_length = res.headers.get("Content-Length")
        if content_length and content_length.isdigit() and int(content_length) > MAX_DOWNLOAD_BYTES:
            return False
        return True

    def _is_audio_asset(self, asset: dict) -> bool:
        source_db = str(asset.get("source_db", "")).lower()
        db_type = str(asset.get("type", "")).lower()
        if source_db in {"cloud_music_library.csv", "cloud_sound_effects.csv"}:
            return True
        return any(k in db_type for k in ["music", "audio", "sound", "bgm", "音效", "歌曲", "歌"])

    def _infer_extension(self, asset: dict, url: str, content_type: str = "") -> str:
        mime_type_hint = ""
        try:
            parsed = urlparse(url)
            mime_type_hint = (parse_qs(parsed.query).get("mime_type", [""])[0] or "").lower()
        except Exception:
            mime_type_hint = ""

        content_type = (content_type or "").lower()
        is_audio = self._is_audio_asset(asset)

        if "audio" in mime_type_hint or content_type.startswith("audio/"):
            if "mpeg" in mime_type_hint or "mpeg" in content_type:
                return ".mp3"
            if "wav" in mime_type_hint or "wav" in content_type:
                return ".wav"
            if "ogg" in mime_type_hint or "ogg" in content_type:
                return ".ogg"
            return ".m4a"

        if "video" in mime_type_hint or content_type.startswith("video/"):
            return ".mp4"

        if is_audio:
            return ".m4a"
        return ".mp4"

    def download_asset(self, query: str, force: bool = False) -> Optional[str]:
        asset = self.find_asset(query)

        # 官方素材库通道: 静态 CSV 的 CDN URL 早已 403, 必须实时从官方接口拿新签名.
        # 只要 query 是 resource_id 或关键词, 就优先走官方通道; CSV 仅作离线兜底.
        official = self.official
        if official is not None:
            try:
                q = str(query)
                # resource_id / 关键词都交给官方管理器 (它会先查本地缓存再搜全网)
                local = official.download(q, force=force, search=True)
                if local and os.path.exists(local):
                    logger.info("Official asset resolved: %s", local)
                    return local
            except Exception as e:
                logger.warning("Official fetch failed for %s: %s", query, e)

        if not asset:
            logger.warning("Cloud Asset '%s' not found (local DB + official API).", query)
            return None

        eid = asset["id"]
        safe_name = "".join([c for c in asset["name"] if c.isalnum() or c in (" ", "_")]).strip()

        url = asset.get("url")
        if (not url) or force:
            url = self.get_url_from_logs(eid)

        if not url:
            logger.warning("No valid download URL found for ID %s.", eid)
            return None
        if not self._is_safe_download_url(url):
            logger.warning("Unsafe download URL blocked for ID %s: %s", eid, url)
            return None

        ext = self._infer_extension(asset, url=url)
        local_filename = f"{eid}_{safe_name}{ext}"
        local_path = os.path.join(CACHE_DIR, local_filename)
        legacy_mp4_path = os.path.join(CACHE_DIR, f"{eid}_{safe_name}.mp4")

        if not force:
            if os.path.exists(local_path):
                return local_path
            if ext != ".mp4" and os.path.exists(legacy_mp4_path):
                try:
                    os.replace(legacy_mp4_path, local_path)
                    return local_path
                except Exception:
                    return legacy_mp4_path

        logger.info("Downloading Cloud Asset: %s", asset["name"])
        try:
            res = requests.get(url, stream=True, timeout=60)
            res.raise_for_status()
            if not self._validate_response_headers(res):
                logger.warning("Download blocked by header validation for ID %s.", eid)
                return None

            ext_from_headers = self._infer_extension(
                asset, url=url, content_type=(res.headers.get("Content-Type") or "")
            )
            if ext_from_headers != ext:
                ext = ext_from_headers
                local_filename = f"{eid}_{safe_name}{ext}"
                local_path = os.path.join(CACHE_DIR, local_filename)

            tmp_path = local_path + ".part"
            total = 0
            with open(tmp_path, "wb") as f:
                for chunk in res.iter_content(chunk_size=32768):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > MAX_DOWNLOAD_BYTES:
                        raise ValueError(f"Download exceeds size limit: {MAX_DOWNLOAD_BYTES} bytes")
                    f.write(chunk)
            os.replace(tmp_path, local_path)
            logger.info("Download finished: %s", local_path)
            return local_path
        except Exception as e:
            part = local_path + ".part"
            if os.path.exists(part):
                try:
                    os.remove(part)
                except Exception:
                    pass
            logger.error("Download error: %s", e)
            return None


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="JianYing Cloud Asset Manager")
    parser.add_argument("query", help="ID or Name of the asset")
    parser.add_argument("--force", action="store_true", help="Force redownload")
    args = parser.parse_args()

    manager = CloudManager()
    path = manager.download_asset(args.query, args.force)
    if path:
        print(f"RESULT_PATH|{path}")
