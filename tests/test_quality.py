"""画质能力 (超清画质 / 补分辨率 / 一键超清) 离线测试.

不需要剪映在跑, 只在临时目录里造最小草稿。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(HERE, "..", "skills", "jianying-editor-11-5")
sys.path.insert(0, os.path.abspath(os.path.join(SKILL, "scripts")))

from jyai import quality as q          # noqa: E402
from jyai import draft as D            # noqa: E402
from jyai import project as P          # noqa: E402


def _read(p):
    with open(p, encoding="utf-8-sig") as f:
        return f.read()


def _video(material_id, w, h, name="v.mp4"):
    return {
        "id": material_id, "material_id": material_id, "material_name": name,
        "path": "C:/m/" + name, "width": w, "height": h, "duration": 3_000_000,
        "type": "video",
        "video_algorithm": {"path": "", "quality_enhance": None,
                            "super_resolution": None,
                            "noise_reduction": None, "deflicker": None},
    }


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="jyai-q-")
        self.root = os.path.join(self.tmp, "com.lveditor.draft")
        self.draft = os.path.join(self.root, "T")
        os.makedirs(self.draft)
        seed = D.skeleton()
        seed["id"] = "DRAFTID"
        seed["name"] = "T"
        seed["canvas_config"] = {"width": 1080, "height": 1920, "ratio": "original"}
        seed.setdefault("materials", {})
        seed["materials"]["videos"] = [ _video("M1", 720, 1280, "a.mp4"),
                                        _video("M2", 1080, 1920, "b.mp4") ]
        seed["tracks"] = [{"type": "video", "segments": [
            {"id": "S1", "material_id": "M1",
             "target_timerange": {"start": 0, "duration": 3_000_000}},
            {"id": "S2", "material_id": "M2",
             "target_timerange": {"start": 3_000_000, "duration": 3_000_000}},
        ], "attribute": 0, "flag": 0, "render_index": 0}]
        seed["function_assistant_info"] = {"fps": {"den": 1, "num": 0},
                                           "enhance_quality": False,
                                           "enhance_quality_fixed": False,
                                           "enhance_quality_segid_list": []}
        D.Draft(self.draft).write_content(seed)
        P.write_timelines(self.draft, seed)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 推断 ----
    def test_infer_standard_canvases(self):
        self.assertEqual(q.infer_target_height({"canvas": {"width": 1080, "height": 1920}}), 1080)
        self.assertEqual(q.infer_target_height({"canvas": {"width": 1920, "height": 1080}}), 1080)
        self.assertEqual(q.infer_target_height({"canvas": {"width": 3840, "height": 2160}}), 2160)
        self.assertEqual(q.infer_target_height({"canvas": {"width": 720, "height": 1280}}), 720)

    def test_infer_long_image_falls_back(self):
        # 长图草稿的画布高度不是导出分辨率
        self.assertEqual(q.infer_target_height({"canvas": {"width": 1920, "height": 3414}}), 1080)
        self.assertEqual(q.infer_target_height({"canvas": {"width": 1080, "height": 6000}}), 1080)

    # ---- 判定 ----
    def test_recommend_needs_upscale(self):
        r = q.recommend(1080, [720, 1080])
        self.assertTrue(r["enable"])
        self.assertTrue(r["est_upload"])
        self.assertEqual(r["min_source_height"], 720)

    def test_recommend_all_sufficient(self):
        r = q.recommend(1080, [1080, 1080, 1440])
        self.assertFalse(r["enable"])
        self.assertFalse(r["est_upload"])

    def test_recommend_4k_source_never_upscales(self):
        r = q.recommend(1080, [3414] * 10)
        self.assertFalse(r["enable"])
        self.assertIn("4K", r["reason"])

    def test_recommend_unknown_source_is_safe(self):
        r = q.recommend(1080, [])
        self.assertFalse(r["enable"])

    def test_recommend_small_upscale_not_worth_it(self):
        # 素材 1000P vs 导出 1080P, 差距 <13%, 不值得上传排队
        r = q.recommend(1080, [1000])
        self.assertFalse(r["enable"])

    # ---- 写入 ----
    def test_apply_quality_enhance_writes_native_schema(self):
        r = q.apply(self.draft, mode="quality_enhance", level=2)
        self.assertEqual(r["stats"]["quality_enhance"], 2)
        c = D.Draft(self.draft).read_content()
        for m in c["materials"]["videos"]:
            self.assertEqual(m["video_algorithm"]["quality_enhance"],
                             {"from": "multi_track", "level": 2})
            self.assertIsNone(m["video_algorithm"]["super_resolution"])

    def test_apply_does_not_touch_draft_level_flag_by_default(self):
        # 实测真实草稿: 片段已开启而该 draft 级标记仍为 False
        # => 它不是生效必需, 默认不能碰
        c0 = D.Draft(self.draft).read_content()
        c0["function_assistant_info"]["enhance_quality"] = False
        D.Draft(self.draft).write_content(c0)
        q.apply(self.draft, mode="quality_enhance", level=2)
        c = D.Draft(self.draft).read_content()
        self.assertFalse(c["function_assistant_info"]["enhance_quality"])
        self.assertEqual(c["function_assistant_info"]["enhance_quality_segid_list"], [])
        for m in c["materials"]["videos"]:
            self.assertIsNotNone(m["video_algorithm"]["quality_enhance"])

    def test_optin_writes_draft_level_flag(self):
        q.apply(self.draft, mode="quality_enhance", level=2, set_draft_flag=True)
        c = D.Draft(self.draft).read_content()
        fa = c["function_assistant_info"]
        self.assertTrue(fa["enhance_quality"])
        self.assertEqual(sorted(fa["enhance_quality_segid_list"]), ["S1", "S2"])

    def test_apply_preserves_unrelated_video_algorithm_keys(self):
        q.apply(self.draft, mode="quality_enhance", level=2)
        c = D.Draft(self.draft).read_content()
        va = c["materials"]["videos"][0]["video_algorithm"]
        for k in ("noise_reduction", "deflicker", "path"):
            self.assertIn(k, va)

    def test_apply_super_resolution_sets_both(self):
        r = q.apply(self.draft, mode="super_resolution", level=2)
        self.assertEqual(r["stats"]["super_resolution"], 2)
        self.assertTrue(r["ultra_hd"])
        c = D.Draft(self.draft).read_content()
        sr = c["materials"]["videos"][0]["video_algorithm"]["super_resolution"]
        self.assertEqual(sr["from"], "multi_track")
        self.assertTrue(sr["4x_mode"])

    def test_apply_off_clears_everything(self):
        q.apply(self.draft, mode="super_resolution", level=2)
        q.apply(self.draft, mode="off")
        c = D.Draft(self.draft).read_content()
        for m in c["materials"]["videos"]:
            self.assertIsNone(m["video_algorithm"]["quality_enhance"])
            self.assertIsNone(m["video_algorithm"]["super_resolution"])
        self.assertFalse(c["function_assistant_info"]["enhance_quality"])
        self.assertEqual(c["function_assistant_info"]["enhance_quality_segid_list"], [])

    def test_apply_idempotent(self):
        q.apply(self.draft, mode="quality_enhance", level=2)
        p = os.path.join(self.draft, "draft_content.json")
        with open(p, "rb") as f:
            a = f.read()
        q.apply(self.draft, mode="quality_enhance", level=2)
        with open(p, "rb") as f:
            self.assertEqual(a, f.read())

    def test_apply_auto_skips_when_sources_sufficient(self):
        c = D.Draft(self.draft).read_content()
        for m in c["materials"]["videos"]:
            m["width"], m["height"] = 1080, 1920
        D.Draft(self.draft).write_content(c)
        r = q.apply(self.draft, mode="auto", target_height=1080)
        self.assertEqual(r["mode"], "off")
        self.assertFalse(r["ultra_hd"])

    def test_dry_run_changes_nothing(self):
        p = os.path.join(self.draft, "draft_content.json")
        with open(p, "rb") as f:
            a = f.read()
        r = q.apply(self.draft, mode="super_resolution", level=2, dry_run=True)
        with open(p, "rb") as f:
            self.assertEqual(f.read(), a)
        self.assertTrue(r["would"]["ultra_hd"])

    # ---- attachment_editing.json ----
    def test_ultra_hd_creates_root_and_timeline_copies(self):
        touched = q.set_ultra_hd(self.draft, True)
        self.assertGreaterEqual(len(touched), 1)
        self.assertTrue(q.read_ultra_hd(self.draft))
        for p in touched:
            ed = json.loads(_read(p))["editing_draft"]
            self.assertTrue(ed["is_use_one_click_ultra_hd"])

    def test_ultra_hd_template_has_full_native_schema(self):
        q.set_ultra_hd(self.draft, True)
        p = os.path.join(self.draft, q.ATT_REL)
        ed = json.loads(_read(p))["editing_draft"]
        self.assertGreaterEqual(len(ed), 40)
        for k in ("cover_extra_info", "image_ai_chat_info", "material_edit_session",
                  "is_use_one_click_beauty", "version"):
            self.assertIn(k, ed)

    def test_ultra_hd_preserves_sibling_schema(self):
        # 先造一个 45 键的兄弟文件, 再写超清, 其余键不能丢
        tl = os.path.join(self.draft, "Timelines")
        tid = os.listdir(tl)[0]
        p = os.path.join(tl, tid, q.ATT_REL)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        probe = {"editing_draft": {"custom_unknown_key": 42, "version": "1.0.0",
                                   "is_use_one_click_ultra_hd": False}}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(probe, f)
        q.set_ultra_hd(self.draft, True)
        ed = json.loads(_read(p))["editing_draft"]
        self.assertEqual(ed["custom_unknown_key"], 42)
        self.assertTrue(ed["is_use_one_click_ultra_hd"])

    # ---- 导出面板 ini ----
    def test_export_panel_roundtrip(self):
        ud = os.path.join(self.tmp, "UserData")
        real = q.jianying_running
        q.jianying_running = lambda: False
        try:
            w = q.set_export_panel(ud, ultra_hd=True, remember_level=3,
                                   mark_sr_used=True)
        finally:
            q.jianying_running = real
        self.assertTrue(w)
        got = q.read_export_panel(ud)
        self.assertEqual(got["export_ini"]["userFirstVideoEnhanceExport"], "true")
        self.assertEqual(got["common"]["quality_enhance_last_choose"], "3")
        self.assertEqual(got["common"]["super_resolution_used"], "true")

    def test_export_panel_skips_when_jianying_running(self):
        ud = os.path.join(self.tmp, "UserData2")
        real = q.jianying_running
        q.jianying_running = lambda: True
        try:
            w = q.set_export_panel(ud, ultra_hd=True)
        finally:
            q.jianying_running = real
        self.assertIn("SKIPPED", w["export"])
        self.assertFalse(os.path.isfile(os.path.join(ud, "Config", q.EXPORT_INI)))

    def test_export_panel_writes_when_not_running(self):
        ud = os.path.join(self.tmp, "UserData3")
        real = q.jianying_running
        q.jianying_running = lambda: False
        try:
            w = q.set_export_panel(ud, ultra_hd=True)
        finally:
            q.jianying_running = real
        self.assertTrue(os.path.isfile(os.path.join(ud, "Config", q.EXPORT_INI)))
        self.assertEqual(q.read_export_panel(ud)["export_ini"]["userFirstVideoEnhanceExport"],
                         "true")

    def test_export_panel_preserves_other_ini_keys(self):
        ud = os.path.join(self.tmp, "UserData")
        cfg = os.path.join(ud, "Config")
        os.makedirs(cfg, exist_ok=True)
        with open(os.path.join(cfg, q.COMMON_INI), "w", encoding="utf-8") as f:
            f.write("[General]\nquick_guid=false\nquality_enhance_last_choose=1\n")
        q.set_export_panel(ud, remember_level=2)
        got = q.read_export_panel(ud)
        self.assertEqual(got["common"]["quick_guid"], "false")
        self.assertEqual(got["common"]["quality_enhance_last_choose"], "2")

    # ---- 状态 ----
    def test_state_reports_native_shapes(self):
        q.apply(self.draft, mode="super_resolution", level=3)
        st = q.state(self.draft)
        self.assertEqual(st["videos"], 2)
        self.assertEqual(len(st["segments"]), 2)
        self.assertEqual(st["segments"][0]["quality_enhance"]["level"], 3)
        self.assertEqual(st["source_heights"], [1280, 1920])
        self.assertEqual(st["source_short_sides"], [720, 1080])

    # ---- 与一句话剪视频的集成 ----
    def test_autocut_quality_off_leaves_draft_untouched(self):
        from jyai import autocut as A
        media = os.path.join(self.tmp, "media")
        os.makedirs(media)
        for n in ("a.mp4", "b.mp4"):
            with open(os.path.join(media, n), "wb") as f:
                f.write(b"\x00" * 1024)
        out = os.path.join(self.tmp, "out")
        os.makedirs(out)
        res = A.autocut(media, "横屏 每段2秒 总共4秒", out_dir=out, name="T_OFF",
                        per_clip=2.0, total=4.0, register=False, limit=2,
                        quality="off")
        self.assertNotIn("quality", res)
        c = D.Draft(os.path.join(out, "T_OFF")).read_content()
        for m in c["materials"]["videos"]:
            va = m.get("video_algorithm") or {}
            self.assertIsNone(va.get("quality_enhance"))

    def test_autocut_quality_forced_super_resolution(self):
        from jyai import autocut as A
        media = os.path.join(self.tmp, "media2")
        os.makedirs(media)
        with open(os.path.join(media, "a.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)
        out = os.path.join(self.tmp, "out2")
        os.makedirs(out)
        res = A.autocut(media, "横屏 每段2秒", out_dir=out, name="T_SR",
                        per_clip=2.0, total=2.0, register=False, limit=1,
                        quality="super_resolution")
        self.assertIsNone(res.get("quality_error"), res.get("quality_error"))
        qr = res["quality"]
        self.assertEqual(qr["mode"], "super_resolution")
        self.assertGreaterEqual(qr["stats"]["super_resolution"], 1)
        c = D.Draft(os.path.join(out, "T_SR")).read_content()
        sr = c["materials"]["videos"][0]["video_algorithm"]["super_resolution"]
        self.assertEqual(sr["from"], "multi_track")

    def test_autocut_quality_failure_does_not_break_editing(self):
        from jyai import autocut as A
        media = os.path.join(self.tmp, "media3")
        os.makedirs(media)
        with open(os.path.join(media, "a.mp4"), "wb") as f:
            f.write(b"\x00" * 1024)
        out = os.path.join(self.tmp, "out3")
        os.makedirs(out)
        real = q.apply
        q.apply = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            res = A.autocut(media, "横屏 每段2秒", out_dir=out, name="T_ERR",
                            per_clip=2.0, total=2.0, register=False, limit=1,
                            quality="super_resolution")
        finally:
            q.apply = real
        self.assertIn("quality_error", res)
        # 主剪辑链路必须照常产出
        c = D.Draft(os.path.join(out, "T_ERR")).read_content()
        self.assertEqual(len(c["materials"]["videos"]), 1)

    def test_normalize_level_accepts_aliases(self):
        self.assertEqual(q.normalize_level("超清"), 2)
        self.assertEqual(q.normalize_level("AI HD"), 3)
        self.assertEqual(q.normalize_level("高清"), 1)
        self.assertEqual(q.normalize_level(9), 3)
        self.assertEqual(q.normalize_level(0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
