"""剪映 11.5 官方素材库 API 客户端.

逆向来源 (全部为剪映 11.5.0.14471 自带资源, 实测可用):
  * Resources/canvas_agent/app/static/js/ai-creation~27.*.js
        -> postArtist / getArtistRequestConfig / fetchMaterialPanel /
           fetchOfficialMaterials / searchArtistEffects / resolveOnlineAssetUrl
  * res_pool.dll  (rsp_cli / ArtistsPanelInfoRequest / ArtistsCategoryEffectsRequest)
        -> ArtistsPanelInfoRequest 请求字段: get_resource, resource_count,
           get_res_category_count, _filter_paid_type, _only_commercial, panel_source
        -> 端点: /artist/v1/panel/get_panel_info,
                 /artist/v1/effect/get_resources_by_category_id,
                 /artist/v1/effect/search, /artist/v1/effect/mget_item

结论 (实测):
  * Host  : https://lv-api.ulikecam.com
  * 鉴权  : 仅需 query 里的 aid=3704 + effect_sdk_version; 不绑定机器/账号,
            无需 sign / tdid / cookie.
  * 素材列表 : /artist/v1/effect/get_resources_by_category_id
               body 里 category_id + category_key + panel + offset/count,
               common_attr.download_info.url 是**当场签发的 CDN 直链** (实测 200).
  * 素材搜索 : /artist/v1/effect/search  (effect_type=201 对应素材库)
  * 单素材   : /artist/v1/effect/mget_item  (items:[{effect_type,id,source}])
  * 音乐     : /lv/v1/get_collections, /lv/v1/get_collection_songs  (需
               Business-Sign-Version: v2 + lan + loc 头)
  * 音效     : panel="audio" 走 get_resources_by_category_id;
               /lv/v1/get_music_effect_collections 取音效合集

这个模块只依赖 requests (项目既有依赖).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional

import requests

# ---------------------------------------------------------------------------
# 常量 (从二进制/前端提取, 实测确定)
# ---------------------------------------------------------------------------
HOST = os.getenv("JY_OFFICIAL_API_HOST", "https://lv-api.ulikecam.com")

# 面板名: 前端常量 k = "material-lib"
PANEL_MATERIAL_LIB = "material-lib"
PANEL_AUDIO = "audio"
PANEL_INSERT = "insert"

# 素材库搜索用的 effect_type (前端 searchArtistEffects 传 201, scene=material_lib_c_v2)
EFFECT_TYPE_MATERIAL_LIB = 201
# 音频 effect_type
EFFECT_TYPE_SOUND = 3
EFFECT_TYPE_MUSIC = 4
EFFECT_TYPE_MATERIAL = 5
EFFECT_TYPE_VOICE = 11

# 已经实测确认可用的面板 (get_panel_info 返回 ret=0)
KNOWN_PANELS = (
    "material-lib",      # 视频/图片素材库 (片头/片尾/热梗/背景/转场/绿幕...)
    "audio",             # 音频面板 (音乐 + 音效)
    "sticker",           # 贴纸
    "text",              # 文字
    "filter",            # 滤镜
    "effects2",          # 特效
    "text-template",     # 文字模板
    "transition",        # 转场
    "music",
    "sound",
)

# 音频接口需要的额外头 (前端 postPcAudio 里对 get_collection_songs/search/songs 加的)
AUDIO_HEADERS = {
    "Business-Sign-Version": "v2",
    "lan": "zh-Hans",
    "loc": "CN",
}

DEFAULT_TIMEOUT = 25.0
DEFAULT_CACHE_TTL = 600  # 秒; 面板/分类变化很慢


def _base_query() -> Dict[str, str]:
    """getArtistRequestConfig 的 params 部分; aid + effect_sdk_version 是硬要求."""
    return {
        "aid": "3704",
        "biz_id": "2",
        "device_platform": "windows",
        "subdivision_id": "",
        "app_name": "JianyingPro",
        "version_code": "11.5.0",
        "version_name": "11.5.0",
        "version_code_num": "722176",
        "language": "zh-Hans",
        "os_version": "10.0.26200",
        "region": "CN",
        "effect_sdk_version": "22.2.0.99999-revision.c0e28546",
    }


class OfficialAPIError(RuntimeError):
    """官方接口返回了非 0 的 ret."""

    def __init__(self, ret: Any, errmsg: str = "", payload: Optional[dict] = None):
        self.ret = ret
        self.errmsg = errmsg
        self.payload = payload or {}
        super().__init__("ret=%s %s" % (ret, errmsg))


class OfficialMaterialAPI:
    """剪映官方素材库同步客户端."""

    def __init__(self, host: str = HOST, timeout: float = DEFAULT_TIMEOUT,
                 device_id: Optional[str] = None, session: Optional[requests.Session] = None,
                 cache_dir: Optional[str] = None, cache_ttl: int = DEFAULT_CACHE_TTL):
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.query = _base_query()
        if device_id:
            self.query["device_id"] = str(device_id)
        self.cache_ttl = cache_ttl
        self.cache_dir = cache_dir or os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or ".",
            "jyai_official_api_cache",
        )
        self._mem: Dict[str, Any] = {}

    # -- 底层 ---------------------------------------------------------------
    def _cache_key(self, path: str, body: Any) -> str:
        try:
            payload = json.dumps({"p": path, "b": body}, ensure_ascii=False, sort_keys=True)
        except Exception:
            payload = "%s:%r" % (path, body)
        return payload

    def _cache_get(self, key: str) -> Optional[Any]:
        hit = self._mem.get(key)
        if hit and time.time() - hit[0] < self.cache_ttl:
            return hit[1]
        return None

    def _cache_put(self, key: str, value: Any) -> None:
        self._mem[key] = (time.time(), value)

    def post(self, path: str, body: dict, *, headers: Optional[dict] = None,
             with_panel: Optional[str] = None, panel_source: Optional[str] = None,
             allow_ret: Iterable[Any] = (0, "0")) -> dict:
        """POST 一个 artist 接口, 返回整个 JSON.

        :param with_panel: 非 None 时往 body 注入 panel / panel_source (postArtist 行为)
        :param allow_ret: 视为成功的 ret 值
        """
        payload: Dict[str, Any] = {"app_id": 3704}
        if with_panel is not None:
            payload["panel"] = with_panel
            if panel_source:
                payload["panel_source"] = panel_source
        payload.update(body or {})

        hdrs = {
            "Content-Type": "application/json",
            "User-Agent": "JianyingPro/11.5.0 (Windows)",
        }
        if headers:
            hdrs.update(headers)

        r = self.session.post(self.host + path, params=self.query, json=payload,
                              headers=hdrs, timeout=self.timeout)
        r.raise_for_status()
        try:
            j = r.json()
        except ValueError:
            raise OfficialAPIError(None, "non-json response: %s" % r.text[:200])
        ret = j.get("ret")
        if allow_ret is not None and ret not in set(allow_ret):
            raise OfficialAPIError(ret, str(j.get("errmsg") or j.get("message") or ""), j)
        return j

    # -- 面板 / 分类 --------------------------------------------------------
    def panel_categories(self, panel: str = PANEL_MATERIAL_LIB,
                         resource_count: int = 50) -> List[dict]:
        """取面板的全部分类 (get_panel_info)."""
        key = self._cache_key("panel:" + panel, resource_count)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        body = {
            "category_status": 1,
            "filter_optional": {"filter_uncommercial": False, "no_copyrighted": False},
            "get_res_category_count": 1,
            "get_resource": True,
            "only_commercial": False,
            "pack_optional": {"need_favorite_info": True, "only_commercial": False},
            "resource_count": resource_count,
            "statistics_optional": {},
        }
        j = self.post("/artist/v1/panel/get_panel_info", body,
                      with_panel=panel, allow_ret=(0, "0"))
        data = j.get("data") or {}
        cats = data.get("categories") or []
        self._cache_put(key, cats)
        return cats

    def panel_source(self, panel: str = PANEL_MATERIAL_LIB) -> str:
        """返回 panel_source (heycan / loki), 影响二值化兜底时的取值."""
        try:
            j = self.post("/artist/v1/panel/get_panel_info", {
                "category_status": 1,
                "filter_optional": {"filter_uncommercial": False, "no_copyrighted": False},
                "get_res_category_count": 1, "get_resource": True, "only_commercial": False,
                "pack_optional": {"need_favorite_info": True, "only_commercial": False},
                "resource_count": 50, "statistics_optional": {},
            }, with_panel=panel, allow_ret=(0, "0"))
            return ((j.get("data") or {}).get("panel_source") or "")
        except Exception:
            return ""

    # -- 素材列表 ----------------------------------------------------------
    def list_category_items(self, category_id: Any, category_key: Optional[str] = None,
                            *, panel: str = PANEL_INSERT, offset: int = 0,
                            count: int = 50, filter_uncommercial: bool = False,
                            request_id: str = "") -> dict:
        """按分类拉素材 (get_resources_by_category_id)."""
        body = {
            "category_id": int(category_id) if str(category_id).isdigit() else category_id,
            "category_key": category_key if category_key is not None else str(category_id),
            "count": int(count),
            "filter_optional": {
                "filter_paid_type": [],
                "filter_uncommercial": bool(filter_uncommercial),
                "no_copyrighted": False,
                "no_tuchong_order": False,
                "only_enterprise_commercial": False,
            },
            "full_count": False,
            "offset": int(offset),
            "pack_optional": {
                "fav_scene": None,
                "image_pack_param": None,
                "large_image_formats": [],
                "need_collection_id": False,
                "need_contract": False,
                "need_favorite_info": True,
                "need_operation_tag": False,
                "need_parent_tag": False,
                "need_tag": False,
                "need_thumb": False,
                "only_commercial": bool(filter_uncommercial),
                "tag_one_level": None,
            },
            "replicate_sdk_version": "",
            "request_id": request_id or "",
            "statistics_optional": {
                "need_add_count": False,
                "need_favorite_count": False,
                "need_usage_count": False,
            },
        }
        j = self.post("/artist/v1/effect/get_resources_by_category_id", body,
                      with_panel=panel, allow_ret=(0, "0"))
        return j.get("data") or {}

    # -- 素材搜索 ----------------------------------------------------------
    def search(self, query: str, *, count: int = 20, offset: int = 0,
               effect_type: int = EFFECT_TYPE_MATERIAL_LIB,
               filter_uncommercial: bool = False, search_id: str = "",
               scene: str = "material_lib_c_v2") -> dict:
        """搜索素材 (searchArtistEffects)."""
        search_option: Dict[str, Any] = {"filter_uncommercial": bool(filter_uncommercial)}
        if effect_type == EFFECT_TYPE_MATERIAL_LIB:
            search_option["scene"] = scene
            search_option["sticker_type"] = 0
        body = {
            "count": int(count),
            "effect_type": int(effect_type),
            "filter_optional": {"filter_paid_type": []},
            "need_recommend": False,
            "offset": int(offset),
            "pack_optional": {"need_contract": True, "need_favorite_info": True},
            "query": query,
            "search_id": search_id or "",
            "search_option": search_option,
            "statistics_optional": {},
            "strategy_extra": "",
        }
        j = self.post("/artist/v1/effect/search", body, allow_ret=(0, "0"))
        return j.get("data") or {}

    # -- 单素材详情 --------------------------------------------------------
    def item_detail(self, resource_id: Any, effect_type: int = EFFECT_TYPE_MATERIAL,
                    source: int = 1) -> Optional[dict]:
        """按 resource_id 取单素材 (mget_item), 会带上当场签发的 download_info."""
        rid = str(resource_id)
        item = {"effect_type": int(effect_type), "id": rid}
        if source is not None:
            item["source"] = int(source)
        body = {
            "items": [item],
            "pack_optional": {"need_contract": True, "need_favorite_info": True},
        }
        j = self.post("/artist/v1/effect/mget_item", body, allow_ret=(0, "0"))
        lst = (j.get("data") or {}).get("effect_item_list") or []
        for it in lst:
            ca = it.get("common_attr") or {}
            if str(ca.get("id") or "") == rid:
                return it
        return lst[0] if lst else None

    # -- 音乐 --------------------------------------------------------------
    def music_collections(self, scene: int = 0) -> List[dict]:
        """音乐合集 (/lv/v1/get_collections)."""
        j = self.post("/lv/v1/get_collections", {"scene": int(scene)},
                      headers=AUDIO_HEADERS, allow_ret=(0, "0"))
        d = j.get("data") or j
        return d.get("collections") or d.get("playlists") or []

    def sound_collections(self, filter_commercial: bool = False) -> List[dict]:
        """音效合集 (/lv/v1/get_music_effect_collections)."""
        j = self.post("/lv/v1/get_music_effect_collections",
                      {"filter_commercial": bool(filter_commercial)},
                      headers=AUDIO_HEADERS, allow_ret=(0, "0"))
        d = j.get("data") or j
        return d.get("collections") or d.get("playlists") or []

    def collection_songs(self, collection_id: Any, *, count: int = 50, offset: int = 0,
                         scene: int = 0, filter_commercial: bool = False,
                         request_id: str = "") -> dict:
        """合集内歌曲 (/lv/v1/get_collection_songs)."""
        cid = collection_id
        if isinstance(cid, str) and cid.isdigit():
            cid = int(cid) if int(cid) <= 2 ** 53 - 1 else cid
        body = {
            "count": int(count),
            "filter_commercial": bool(filter_commercial),
            "filter_paid_type": [],
            "id": cid,
            "offset": int(offset),
            "only_enterprise_commercial": False,
            "scene": int(scene),
        }
        if request_id:
            body["request_id"] = request_id
        j = self.post("/lv/v1/get_collection_songs", body, headers=AUDIO_HEADERS,
                      allow_ret=(0, "0"))
        return j.get("data") or j

    def search_songs(self, keyword: str, *, count: int = 20, offset: int = 0,
                     scene: int = 3, filter_commercial: bool = False,
                     search_id: str = "") -> dict:
        """搜索歌曲 (/lv/v1/search/songs). 参数名是 keyword, 不是 query (实测)."""
        body: Dict[str, Any] = {
            "count": int(count),
            "offset": int(offset),
            "keyword": keyword,
            "scene": int(scene),
            "filter_commercial": bool(filter_commercial),
            "filter_paid_type": [],
            "only_enterprise_commercial": False,
        }
        if search_id:
            body["search_id"] = search_id
        j = self.post("/lv/v1/search/songs", body, headers=AUDIO_HEADERS, allow_ret=(0, "0"))
        return j.get("data") or j

    def sound_items(self, category_id: Any, category_key: Optional[str] = None,
                    *, offset: int = 0, count: int = 50,
                    filter_uncommercial: bool = False) -> dict:
        """音效面板某分类的素材 (panel=audio 走 get_resources_by_category_id)."""
        return self.list_category_items(
            category_id, category_key, panel=PANEL_AUDIO, offset=offset, count=count,
            filter_uncommercial=filter_uncommercial)

    # -- 规范化 ------------------------------------------------------------
    @staticmethod
    def normalize_item(item: dict) -> dict:
        """把 effect_item_list 里的一条压成统一结构, 便于外部使用."""
        ca = (item or {}).get("common_attr") or {}
        author = (item or {}).get("author") or {}
        cover = ca.get("cover_url") or {}
        dl = ca.get("download_info") or {}
        item_urls = ca.get("item_urls") or []
        return {
            "resource_id": str(ca.get("id") or ""),
            "effect_id": str(ca.get("effect_id") or ""),
            "effect_type": ca.get("effect_type"),
            "source": ca.get("source"),
            "title": ca.get("title") or "",
            "description": ca.get("description") or "",
            "duration": ca.get("duration") or 0,
            "md5": ca.get("md5") or "",
            "cover_url": cover.get("small") or cover.get("static_img") or "",
            "cover_static": cover.get("static_img") or "",
            "detail_url": item_urls[0] if item_urls else "",
            "download_url": dl.get("url") or "",
            "download_format": dl.get("format") or "",
            "business_info": ca.get("business_info") or {},
            "extra": ca.get("extra") or "",
            "author": author.get("name") or "",
            "author_id": str(author.get("uid") or ""),
            "category_ids": ca.get("category_ids") or [],
            "publish_source": ca.get("publish_source") or "",
        }

    @classmethod
    def iter_normalized(cls, effect_item_list: Iterable[dict]) -> List[dict]:
        return [cls.normalize_item(it) for it in (effect_item_list or [])]


def make_default_api(**kwargs) -> OfficialMaterialAPI:
    """按本机剪映安装情况构造一个 API 客户端 (device_id 从 bridge 里读, 可选)."""
    if "device_id" not in kwargs:
        try:
            from jyai.bridge import discover  # 同目录脚本, 运行时才 import
            info = discover(require_alive=False) or {}
            pid = info.get("pid")
            if pid:
                kwargs["device_id"] = None  # 不是必须, 留 None 即可
        except Exception:
            pass
    return OfficialMaterialAPI(**kwargs)