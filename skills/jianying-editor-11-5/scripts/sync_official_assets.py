"""重新同步剪映官方素材库索引 (替换 data/cloud_*.csv 里失效的 CDN URL).

背景:
  data/cloud_video_assets.csv 等文件里的 URL 是 2026-03 签发的 CDN 直链,
  签名早已过期, 全部返回 403. 官方素材必须**实时**取新签名.

本脚本做的事:
  1. 遍历 material-lib / audio / sticker / filter / effects2 / text-template 等面板;
  2. 拉取每个分类下的素材 (resource_id, 标题, 类型, 时长, 作者);
  3. 写入 data/cloud_video_assets.csv / cloud_music_library.csv /
     cloud_sound_effects.csv, url 列留空 (下载时由 official_asset_manager 动态取);
  4. 附带写出 data/official_panels.json 记录分类树.

用法:
  python scripts/sync_official_assets.py            # 全量
  python scripts/sync_official_assets.py --quick     # 只同步每个面板首页
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from jy_official_api import (  # noqa: E402
    OfficialMaterialAPI, EFFECT_TYPE_SOUND, PANEL_MATERIAL_LIB, PANEL_AUDIO,
)

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(SKILL_ROOT, "data")

# 需要抓取的面板 -> (输出文件, kind)
PANEL_TARGETS = [
    (PANEL_MATERIAL_LIB, "cloud_video_assets.csv", "video"),
    ("sticker", "cloud_video_assets.csv", "sticker"),
    ("filter", "cloud_video_assets.csv", "filter"),
    ("effects2", "cloud_video_assets.csv", "effect"),
    ("text-template", "cloud_video_assets.csv", "text_template"),
    (PANEL_AUDIO, "cloud_sound_effects.csv", "sound"),
]


def _kind_of(item: dict) -> str:
    et = item.get("effect_type")
    if et in (EFFECT_TYPE_SOUND, 3, 4, 11):
        return "audio"
    return "video"


def crawl_panel(api: OfficialMaterialAPI, panel: str, kind: str,
                quick: bool, max_pages: int = 4) -> list:
    out = []
    cats = api.panel_categories(panel)
    for c in cats:
        cid, ckey = c.get("category_id"), c.get("category_key")
        offset = 0
        pages = 0
        while True:
            try:
                d = api.list_category_items(cid, ckey, panel=panel, offset=offset, count=50)
            except Exception as e:
                print("   warn: %s/%s offset=%s: %s" % (panel, cid, offset, str(e)[:60]))
                break
            items = api.iter_normalized(d.get("effect_item_list") or [])
            for it in items:
                it["_panel"] = panel
                it["_category"] = c.get("category_name") or ""
                it["_category_id"] = cid
                out.append(it)
            if not items or not d.get("has_more"):
                break
            if quick:
                break
            offset = d.get("next_offset") or (offset + len(items))
            pages += 1
            if pages >= max_pages:
                break
        print("   %-16s %-14s +%d" % (panel, (c.get("category_name") or "")[:14], len(out)))
    return out


def write_csv(path: str, header_comment: list, fieldnames: list, rows: list) -> None:
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for c in header_comment:
            f.write(c + "\n")
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})
    os.replace(tmp, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="同步剪映官方素材库索引")
    ap.add_argument("--quick", action="store_true", help="每个分类只抓第一页")
    ap.add_argument("--json", action="store_true", help="只导出 official_panels.json")
    a = ap.parse_args(argv)

    api = OfficialMaterialAPI()
    os.makedirs(DATA_DIR, exist_ok=True)

    # 先导出分类树
    panel_tree = {}
    for panel, _, _ in PANEL_TARGETS:
        try:
            cats = api.panel_categories(panel)
            panel_tree[panel] = [
                {"category_id": c.get("category_id"), "category_name": c.get("category_name"),
                 "category_key": c.get("category_key")}
                for c in cats
            ]
        except Exception as e:
            print("panel %s failed: %s" % (panel, e))
    with io.open(os.path.join(DATA_DIR, "official_panels.json"), "w", encoding="utf-8") as f:
        json.dump(panel_tree, f, ensure_ascii=False, indent=2)
    print("分类树已写入 data/official_panels.json (%d panels)" % len(panel_tree))
    if a.json:
        return 0

    video_rows, sound_rows = [], []
    seen_ids = set()
    for panel, out_file, kind in PANEL_TARGETS:
        print("crawl panel=%s -> %s" % (panel, out_file))
        items = crawl_panel(api, panel, kind, a.quick)
        for it in items:
            rid = it.get("resource_id")
            if not rid or rid in seen_ids:
                continue
            seen_ids.add(rid)
            row = {
                "id": rid,
                "name": it.get("title") or "",
                "type": it.get("_category") or kind,
                "duration": "%.2f" % (it.get("duration", 0) or 0),
                "url": "",  # 动态获取, 不写静态签名
                "effect_type": it.get("effect_type"),
                "source": it.get("source"),
                "panel": panel,
            }
            if kind == "sound":
                # tools/validate_data_schema.py 要求 effect_id/title/duration_s/categories
                sound_rows.append({
                    "effect_id": rid,
                    "title": it.get("title") or "",
                    "duration_s": "%.2f" % (it.get("duration", 0) or 0),
                    "categories": it.get("_category") or kind,
                    "url": "",
                    "effect_type": it.get("effect_type"),
                    "source": it.get("source"),
                    "panel": panel,
                })
            else:
                video_rows.append(row)

    # 音乐库: 从合集里抓
    music_rows = []
    try:
        for col in api.music_collections():
            try:
                d = api.collection_songs(col.get("id"), count=50, scene=0)
            except Exception:
                continue
            for s in (d.get("songs") or []):
                sid = str(s.get("id") or "")
                if not sid or ("m" + sid) in seen_ids:
                    continue
                seen_ids.add("m" + sid)
                music_rows.append({
                    "music_id": sid,
                    "title": s.get("title") or "",
                    "duration_s": "%.2f" % (s.get("duration") or 0),
                    "categories": col.get("name") or "",
                    "url": "",
                })
            print("   music collection %-16s +%d" % ((col.get("name") or "")[:16], len(music_rows)))
    except Exception as e:
        print("music crawl failed: %s" % e)

    write_csv(
        os.path.join(DATA_DIR, "cloud_video_assets.csv"),
        ["# JianYing Official Material Index (dynamic signed URLs, fetched at download time)",
         "# AI Guidance: id is the platform resource_id. Use jyai cloud-get <id> or add_cloud_media(<id>).",
         "# url column is intentionally empty: official CDN links are short-lived and must be requested live."],
        ["id", "name", "type", "duration", "url", "effect_type", "source", "panel"],
        video_rows,
    )
    write_csv(
        os.path.join(DATA_DIR, "cloud_sound_effects.csv"),
        ["# JianYing Official Sound Effects Index (dynamic signed URLs)",
         "# AI Guidance: use effect_id with jyai cloud-get <id> or add_cloud_music(<id>).",
         "# url column is intentionally empty: official CDN links are short-lived."],
        ["effect_id", "title", "duration_s", "categories", "url", "effect_type", "source", "panel"],
        sound_rows,
    )
    write_csv(
        os.path.join(DATA_DIR, "cloud_music_library.csv"),
        ["# JianYing Official Music Library Index (dynamic signed URLs)",
         "# AI Guidance: use music_id with jyai cloud-get <id>.",
         "# url column is intentionally empty: official CDN links are short-lived."],
        ["music_id", "title", "duration_s", "categories", "url"],
        music_rows,
    )
    print("\n完成: video=%d sound=%d music=%d" % (len(video_rows), len(sound_rows), len(music_rows)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())