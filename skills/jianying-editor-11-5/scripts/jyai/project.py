"""草稿注册表 (root_meta_info.json) 与 11.x Timelines 目录结构.

剪映 11.x 的草稿索引不在草稿目录里, 而在:
    <LOCALAPPDATA>/JianyingPro/User Data/Projects/com.lveditor.draft/root_meta_info.json
结构:
    {
      "all_draft_store": [ {draft_id, draft_name, draft_fold_path, draft_json_file,
                            draft_root_path, tm_draft_create, tm_draft_modified,
                            tm_duration, ...}, ... ],
      "draft_ids": 28,  # 本机 11.5 是整数计数; 保留原值
      "root_path": ".../Projects/com.lveditor.draft"
    }
手工造出来的草稿目录不会出现在列表里, 必须往 all_draft_store 里补一条.

11.x 每个草稿内部又多了一层 Timelines:
    <draft>/Timelines/project.json           时间线索引 (明文)
    <draft>/Timelines/<timeline_id>/draft_content.json    该时间线内容(会加密)
    <draft>/Timelines/<timeline_id>/template.json         明文副本(模板态)
    <draft>/Timelines/<timeline_id>/template.tmp          官方空草稿骨架(明文)
"""
from __future__ import annotations
import json, os, shutil, time, uuid
from typing import Any, Dict, List, Optional

SKELETON_MIN = 3941          # template.tmp 官方空骨架大小, 用于校验


def user_data_dir() -> str:
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
        "JianyingPro", "User Data")


def root_meta_path(user_data: Optional[str] = None) -> str:
    base = user_data or user_data_dir()
    return os.path.join(base, "Projects", "com.lveditor.draft", "root_meta_info.json")


def load_root_meta(user_data: Optional[str] = None) -> Dict[str, Any]:
    p = root_meta_path(user_data)
    if not os.path.isfile(p):
        return {"all_draft_store": [], "draft_ids": 0, "root_path": os.path.dirname(p)}
    with open(p, "rb") as f:
        raw = f.read()
    return json.loads(raw.decode("utf-8-sig"))


def save_root_meta(meta: Dict[str, Any], user_data: Optional[str] = None) -> str:
    p = root_meta_path(user_data)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    bak = p + ".jyai.%s.bak" % time.strftime("%Y%m%d%H%M%S")
    if os.path.isfile(p):
        shutil.copy2(p, bak)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(meta, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, p)
    return p


def read_meta_id(draft_dir: str) -> Optional[str]:
    """读草稿自己的 draft_meta_info.json 里的 draft_id.

    必须用它 —— 剪映打开草稿时会拿注册表里的 draft_id 去和
    draft_meta_info.json 里的比对, 不一致就打不开.
    """
    p = os.path.join(draft_dir, "draft_meta_info.json")
    try:
        from .draft import load_json
        return (load_json(p) or {}).get("draft_id") or None
    except Exception:
        return None


def entry_for(draft_dir: str, name: Optional[str] = None,
              duration: int = 0, cover: str = "",
              draft_id: Optional[str] = None) -> Dict[str, Any]:
    """按实测字段集构造一条 all_draft_store 记录."""
    draft_dir = os.path.abspath(draft_dir)
    now = int(time.time() * 1_000_000)
    fwd = draft_dir.replace("\\", "/")
    return {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "draft_cloud_last_action_download": False,
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": cover or (fwd + "\\draft_cover.jpg"),
        "draft_fold_path": fwd,
        "draft_id": (draft_id or read_meta_id(draft_dir) or str(uuid.uuid4())).upper(),
        "draft_is_ai_shorts": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_json_file": fwd + "\\draft_content.json",
        "draft_name": name or os.path.basename(draft_dir),
        "draft_new_version": "",
        "draft_root_path": os.path.dirname(fwd),
        "draft_timeline_materials_size": materials_size(draft_dir),
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "streaming_edit_draft_ready": True,
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now,
        "tm_draft_modified": now,
        "tm_draft_removed": 0,
        "tm_duration": int(duration),
    }


def register(draft_dir: str, name: Optional[str] = None, duration: int = 0,
             user_data: Optional[str] = None) -> Dict[str, Any]:
    """按目录更新登记; 保留既有草稿字段和 draft_ids 的类型/值. 时长单位为微秒."""
    draft_dir = os.path.abspath(draft_dir)
    if not os.path.isfile(os.path.join(draft_dir, "draft_content.json")):
        raise ValueError("登记必须指向含 draft_content.json 的具体草稿目录: " + draft_dir)
    meta = load_root_meta(user_data)
    store: List[Dict[str, Any]] = meta.setdefault("all_draft_store", [])
    meta.setdefault("draft_ids", len(store))
    meta.setdefault("root_path", os.path.dirname(root_meta_path(user_data)))
    target = os.path.abspath(draft_dir).replace("\\", "/")
    ent = next((e for e in store if normalize_path(e.get("draft_fold_path", "")) == normalize_path(target)), None)
    fresh = entry_for(draft_dir, name, duration, draft_id=read_meta_id(draft_dir) or (ent or {}).get("draft_id"))
    if ent is None:
        ent = fresh
        store.insert(0, ent)
    else:
        for k, v in fresh.items():
            ent.setdefault(k, v)
        for k in ("draft_cover", "draft_fold_path", "draft_root_path", "draft_json_file",
                  "draft_id", "draft_name", "draft_timeline_materials_size", "tm_duration",
                  "tm_draft_modified", "streaming_edit_draft_ready"):
            ent[k] = fresh[k]
    meta["all_draft_store"] = store
    save_root_meta(meta, user_data)
    return ent


def normalize_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).replace("\\", "/").rstrip("/").lower()


def materials_size(draft_dir: str) -> int:
    """统计时间线引用的本地素材字节数, 去重; 不把 JSON 长度冒充素材大小."""
    cp = os.path.join(draft_dir, "draft_content.json")
    from .draft import load_json
    content = load_json(cp)
    seen, total = set(), 0
    for kind in ("videos", "audios", "images"):
        for material in (content.get("materials") or {}).get(kind, []):
            path = material.get("path") or ""
            if not path:
                continue
            if not os.path.isabs(path):
                path = os.path.join(draft_dir, path)
            key = normalize_path(path)
            if key not in seen and os.path.isfile(path):
                seen.add(key)
                total += os.path.getsize(path)
    return total


def unregister(draft_dir: str, user_data: Optional[str] = None) -> int:
    meta = load_root_meta(user_data)
    target = os.path.abspath(draft_dir).replace("\\", "/")
    store = meta.get("all_draft_store", [])
    before = len(store)
    meta["all_draft_store"] = [e for e in store if e.get("draft_fold_path") != target]
    save_root_meta(meta, user_data)
    return before - len(meta["all_draft_store"])


# --------------------------------------------------------------------------
# Timelines 目录
# --------------------------------------------------------------------------
def write_timelines(draft_dir: str, content: Dict[str, Any],
                    timeline_id: Optional[str] = None,
                    timeline_name: str = "时间线01") -> Dict[str, str]:
    """在草稿目录下建立 11.x 的 Timelines 结构 (全部明文).

    返回 {文件名: 路径}.
    """
    draft_dir = os.path.abspath(draft_dir)
    tid = timeline_id or str(uuid.uuid4()).upper()
    tl = os.path.join(draft_dir, "Timelines")
    tdir = os.path.join(tl, tid)
    os.makedirs(tdir, exist_ok=True)
    now = int(time.time() * 1_000_000)

    data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out: Dict[str, str] = {}

    # 时间线内容(明文; 加密开关关闭后剪映自己也会写明文)
    for rel in ("draft_content.json", "template.json", "draft_content.json.bak",
                "template-2.tmp"):
        p = os.path.join(tdir, rel)
        with open(p, "wb") as f:
            f.write(data)
        out[rel] = p

    # 官方空骨架, 剪映用它判断"草稿可新建时间线"
    skel = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets",
                        "template.skeleton.tmp")
    tp = os.path.join(tdir, "template.tmp")
    if os.path.isfile(skel):
        shutil.copyfile(skel, tp)
    elif not os.path.isfile(tp):
        with open(tp, "wb") as f:
            f.write(b"{}")
    out["template.tmp"] = tp

    # 时间线索引
    proj = {
        "config": {"color_space": -1, "mixed_track_mode_on": False,
                   "render_index_track_mode_on": False, "use_float_render": False},
        "create_time": now,
        "id": str(uuid.uuid4()).upper(),
        "main_timeline_id": tid,
        "timelines": [{"create_time": now, "id": tid, "is_marked_delete": False,
                       "name": timeline_name, "update_time": now}],
        "update_time": now, "version": 0,
    }
    pp = os.path.join(tl, "project.json")
    with open(pp, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False, separators=(",", ":"))
    out["Timelines/project.json"] = pp

    # 当前活动时间线
    lay = {"activeTimeline": tid,
           "dockItems": [{"dockIndex": 0, "ratio": 1, "timelineIds": [tid],
                          "timelineNames": [timeline_name]}],
           "layoutOrientation": 1}
    lp = os.path.join(draft_dir, "timeline_layout.json")
    with open(lp, "w", encoding="utf-8") as f:
        json.dump(lay, f, ensure_ascii=False, separators=(",", ":"))
    out["timeline_layout.json"] = lp

    return out
