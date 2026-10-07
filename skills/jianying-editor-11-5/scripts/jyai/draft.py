"""剪映 11.x 草稿目录的读写.

实测结论 (11.3.0.14362):
    draft_content.json / draft_meta_info.json / draft.extra / project.json
        当本地设置 draft_entryption_config.enable_all = true 时, 全部被加密:
            * 整文件 base64 编码
            * 解出的密文熵 ≈ 8.0, 非 zlib/brotli/zstd/lz4 容器
            * 由 videoeditor.dll 的 EncryptUtils::calcEntropyDynamicString 派生密钥
              + draft_encryption_params 参与, 官方未公开算法
    另有大量**明文**旁路文件同样承载完整时间线信息, 可直接读写:
        <draft>/Timelines/<timeline_id>/template.json     -- 完整时间线 (含 materials/tracks)
        <draft>/Timelines/<timeline_id>/template.tmp      -- 官方空草稿骨架 (3941B, 明文)
        <draft>/subdraft/**/draft_content.json            -- 子草稿, 明文 JSON
        <draft>/Timelines/<timeline_id>/common_attachment/*.json
"""
from __future__ import annotations
import base64, json, os, shutil, time, uuid
from typing import Any, Dict, List, Optional

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def skeleton() -> Dict[str, Any]:
    with open(os.path.join(ASSETS, "draft_content_skeleton.json"), "rb") as f:
        return json.loads(f.read().decode("utf-8"))


def is_base64_blob(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False
    if not head or head[0:1] in (b"{", b"["):
        return False
    allowed = set(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\r\n")
    return all(c in allowed for c in head)


def load_json(path: str) -> Dict[str, Any]:
    with open(path, "rb") as f:
        raw = f.read()
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass
    from .native_crypto import read_bytes
    try:
        return json.loads(read_bytes(path).decode("utf-8-sig"))
    except Exception as native_error:
        pass
    try:
        dec = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("无法解析 %s: 既不是明文 JSON 也不是 base64 容器" % path) from exc
    try:
        return json.loads(dec.decode("utf-8"))
    except Exception as exc:
        raise ValueError(
            "%s 的原生编解码失败; 当前适配需要已安装的 64 位剪映 11.5.0.14471" % path
        ) from exc


class Draft:
    """一个草稿目录."""

    def __init__(self, path: str):
        self.path = os.path.abspath(path)
        if not os.path.isdir(self.path):
            raise FileNotFoundError(self.path)

    # ---- 路径 ----
    @property
    def content_path(self) -> str: return os.path.join(self.path, "draft_content.json")

    @property
    def backup_path(self) -> str: return self.content_path + ".bak"

    @property
    def meta_path(self) -> str: return os.path.join(self.path, "draft_meta_info.json")

    @property
    def virtual_store_path(self) -> str: return os.path.join(self.path, "draft_virtual_store.json")

    @property
    def timelines_dir(self) -> str: return os.path.join(self.path, "Timelines")

    # ---- 状态 ----
    @property
    def is_encrypted(self) -> bool:
        return os.path.isfile(self.content_path) and is_base64_blob(self.content_path)

    @property
    def name(self) -> str:
        return os.path.basename(self.path)

    def timeline_ids(self) -> List[str]:
        if not os.path.isdir(self.timelines_dir):
            return []
        return [d for d in os.listdir(self.timelines_dir)
                if os.path.isdir(os.path.join(self.timelines_dir, d))]

    def active_timeline(self) -> Optional[str]:
        p = os.path.join(self.path, "timeline_layout.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    return json.load(f).get("activeTimeline")
            except Exception:
                return None
        ids = self.timeline_ids()
        return ids[0] if ids else None

    def template_json(self, timeline_id: Optional[str] = None) -> Optional[str]:
        tid = timeline_id or self.active_timeline()
        if not tid:
            return None
        p = os.path.join(self.timelines_dir, tid, "template.json")
        return p if os.path.isfile(p) else None

    # ---- 读 ----
    def read_content(self) -> Dict[str, Any]:
        """优先读实际内容; 11.5 密文用原生 DraftIO 解码, 最后才用模板副本."""
        if os.path.isfile(self.content_path):
            try:
                return load_json(self.content_path)
            except ValueError:
                pass
        tj = self.template_json()
        if tj:
            return load_json(tj)
        raise ValueError(
            "草稿 %s 的 draft_content.json 已加密, 且没有可用的明文 template.json" % self.name
        )

    # ---- 写 ----
    def write_content(self, content: Dict[str, Any]) -> str:
        """写入 draft_content.json (明文), 同时同步 .bak / template-2.tmp / template.json, 保持一致性."""
        data = json.dumps(content, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        from .repair import atomic_bytes
        for p in (self.content_path, self.backup_path,
                  os.path.join(self.path, "template-2.tmp")):
            atomic_bytes(p, data)
        tid = self.active_timeline()
        if tid:
            tdir = os.path.join(self.timelines_dir, tid)
            os.makedirs(tdir, exist_ok=True)
            for rel in ("template.json", "draft_content.json", "draft_content.json.bak", "template-2.tmp"):
                atomic_bytes(os.path.join(tdir, rel), data)
        return self.content_path

    def repair(self, user_data=None, **kwargs):
        from .repair import repair
        return repair(self.path, user_data=user_data, **kwargs)

    def write_meta_name(self) -> None:
        """把 draft_meta_info.json 里的名字/路径刷新为当前实际值."""
        if os.path.isfile(self.meta_path) and is_base64_blob(self.meta_path):
            return
        meta = {}
        if os.path.isfile(self.meta_path):
            try:
                meta = load_json(self.meta_path)
            except Exception:
                meta = {}
        meta.setdefault("draft_id", str(uuid.uuid4()).upper())
        meta["draft_name"] = self.name
        meta["draft_fold_path"] = self.path.replace("\\", "/")
        meta["draft_root_path"] = os.path.dirname(self.path).replace("\\", "/")
        meta["draft_json_file"] = self.content_path.replace("\\", "/")
        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=4)

    # ---- 备份 ----
    def backup(self, tag: str = "") -> str:
        stamp = time.strftime("%Y%m%d%H%M%S")
        dst = os.path.join(self.path, ".backup", "%s_%s.ai.bak" % (stamp, tag or "manual"))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(self.content_path, dst)
        return dst

    def restore(self, bak_path: str) -> None:
        shutil.copy2(bak_path, self.content_path)
        shutil.copy2(bak_path, self.backup_path)


def list_drafts(root: str) -> List[Draft]:
    out = []
    if not os.path.isdir(root):
        return out
    for d in sorted(os.listdir(root)):
        p = os.path.join(root, d)
        if d.startswith(".") or not os.path.isdir(p):
            continue
        if os.path.isfile(os.path.join(p, "draft_content.json")):
            out.append(Draft(p))
    return out


def find_draft(root: str, name: str) -> Draft:
    for d in list_drafts(root):
        if d.name == name:
            return d
    raise FileNotFoundError("草稿 %s 不存在于 %s" % (name, root))
