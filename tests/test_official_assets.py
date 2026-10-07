"""官方素材库通道的离线回归测试 (不依赖网络).

覆盖:
  * jy_official_api 的请求构造 (query 必须带 aid + effect_sdk_version)
  * official_asset_manager 的解析/分类/落盘逻辑
  * cloud_manager 走官方通道优先 (用 stub 替换实现)
  * engine 的云素材 spec 字段能解析成本地路径
"""
import csv
import io
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "jianying-editor-11-5", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import jy_official_api as api_mod          # noqa: E402
import official_asset_manager as oam_mod   # noqa: E402


class OfficialAPITests(unittest.TestCase):
    def test_base_query_has_required_fields(self):
        q = api_mod._base_query()
        # 实测: 没有 aid 或 effect_sdk_version 时后端返回 ret=2009
        self.assertEqual(q["aid"], "3704")
        self.assertTrue(q["effect_sdk_version"])
        self.assertEqual(q["device_platform"], "windows")

    def test_panel_constants_match_frontend(self):
        # 前端常量 k = "material-lib"
        self.assertEqual(api_mod.PANEL_MATERIAL_LIB, "material-lib")
        self.assertEqual(api_mod.PANEL_AUDIO, "audio")
        self.assertEqual(api_mod.EFFECT_TYPE_MATERIAL_LIB, 201)

    def test_post_injects_appid_and_panel(self):
        api = api_mod.OfficialMaterialAPI()
        captured = {}

        class FakeResp:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return {"ret": "0", "data": {}}
            text = ""

        def fake_post(url, params=None, json=None, headers=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["body"] = json
            return FakeResp()

        api.session = mock.Mock()
        api.session.post = fake_post
        api.post("/artist/v1/panel/get_panel_info", {"category_status": 1},
                 with_panel="material-lib")
        self.assertIn("lv-api.ulikecam.com", captured["url"])
        self.assertEqual(captured["body"]["app_id"], 3704)
        self.assertEqual(captured["body"]["panel"], "material-lib")
        self.assertIn("aid", captured["params"])


class AssetManagerTests(unittest.TestCase):
    def test_classify_kind(self):
        self.assertEqual(oam_mod.classify_kind({"effect_type": 3}), "audio")
        self.assertEqual(oam_mod.classify_kind({"effect_type": 4}), "audio")
        self.assertEqual(oam_mod.classify_kind(
            {"effect_type": 5, "download_format": "mp4"}), "video")
        self.assertEqual(oam_mod.classify_kind(
            {"effect_type": 9, "download_format": "png"}), "image")

    def test_safe_name(self):
        self.assertEqual(oam_mod._safe_name("a/b:c*d"), "abcd")
        self.assertTrue(oam_mod._safe_name(""))

    def test_local_artist_effect_lookup(self):
        mgr = oam_mod.OfficialAssetManager(allow_network=False)
        with mock.patch.object(oam_mod, "user_cache_root", return_value=os.path.join(ROOT, "no_such")):
            self.assertEqual(mgr.local_artist_effect_path("123"), "")

    def test_download_prefers_api_url(self):
        mgr = oam_mod.OfficialAssetManager(allow_network=False)
        ref = oam_mod.AssetRef(resource_id="1", title="t", kind="video",
                               download_url="")
        self.assertIsNone(mgr.download_ref(ref))


class CloudManagerWiringTests(unittest.TestCase):
    def test_download_asset_uses_official_channel(self):
        import cloud_manager as cm_mod
        mgr = cm_mod.CloudManager()
        called = {}

        class FakeOfficial:
            def download(self, query, force=False, search=True):
                called["query"] = query
                return r"C:\fake\asset.mp4"

        mgr._official = FakeOfficial()
        with mock.patch.object(cm_mod.os.path, "exists", return_value=True):
            out = mgr.download_asset("7224413118917528884")
        self.assertEqual(called["query"], "7224413118917528884")
        self.assertEqual(out, r"C:\fake\asset.mp4")

    def test_find_asset_allows_rows_without_url(self):
        import cloud_manager as cm_mod
        mgr = cm_mod.CloudManager()
        # 11.5 索引行 url 为空但仍是合法资源 (签名在下载时动态签发)
        mgr.assets = {"999": {"id": "999", "name": "白场", "url": "", "type": "热门"}}
        self.assertIsNotNone(mgr.find_asset("999"))
        self.assertIsNotNone(mgr.find_asset("白场"))


class EngineCloudSpecTests(unittest.TestCase):
    def test_resolve_cloud_material_none_without_fields(self):
        from jyai import engine
        self.assertIsNone(engine.resolve_cloud_material({"path": "a.mp4"}))

    def test_index_files_are_valid(self):
        data_dir = os.path.join(ROOT, "skills", "jianying-editor-11-5", "data")
        for name, id_col in (("cloud_video_assets.csv", "id"),
                             ("cloud_sound_effects.csv", "effect_id"),
                             ("cloud_music_library.csv", "music_id")):
            p = os.path.join(data_dir, name)
            with io.open(p, encoding="utf-8") as f:
                lines = [l for l in f if not l.startswith("#")]
            rows = list(csv.DictReader(lines))
            self.assertGreater(len(rows), 0, name)
            self.assertIn(id_col, rows[0], name)
            # url 必须为空 (动态签发, 不持久化过期签名)
            if "url" in rows[0]:
                non_empty = [r for r in rows if (r.get("url") or "").strip()]
                self.assertEqual(non_empty, [], "%s still has stale static URLs" % name)


if __name__ == "__main__":
    unittest.main()