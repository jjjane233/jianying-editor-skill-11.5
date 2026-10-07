"""关闭 / 开启 剪映本地草稿与素材的加解密开关, 并可加锁防止被重置.

剪映 11.x PC 把开关放在 User Data/MMKV/settings_json 的 key_settings_json 里.
实测 (11.3.0.14362 / 11.5.0.14471) 一共 5 处闸门:

    draft_entryption_config.enable_all      = true   草稿本体 (draft_content.json 等)
    MaterialEncrypt.encry                   = true   素材级加密 (crypto_key_store.dat)
    enable_encrypt_composite                = true   合成/导出中间产物加密
    enable_import_encrypt_file_check        = true   导入外部加密文件校验
    upload_crypto_file_check_tag.enable     = true   上传前加密校验

只要 draft_entryption_config.enable_all 为 true,
draft_content.json / draft_meta_info.json / draft.extra / project.json 就会被
整文件 base64 包一层熵密钥密文 —— 第三方工具无法直接读写.

【重要: 光改文件不管用】
实测每一次启动剪映, 10 秒内这些开关都会被写回 true (配置从服务端下发的).
所以本模块提供三种强度:

    disable()         只改值         -- 启动即失效
    lock()            改值 + 只读    -- 实测可挡住重置, 启动后仍为 false
    unlock()          去掉只读

实测记录 (2026-09-29):
    仅改值        启动后 10s  -> enable_all = True   (被重置)
    改值 + 只读   启动后 60s  -> enable_all = False  (保持, 剪映功能正常)

用法:
    python -m jyai.cli disable-encryption          # 关 + 加锁 (推荐)
    python -m jyai.cli disable-encryption --no-lock
    python -m jyai.cli enable-encryption           # 解锁 + 恢复
    python -m jyai.cli status
"""
from __future__ import annotations
import json, os, shutil, stat, time
from typing import Any, Dict, Optional

from . import mmkv

SETTINGS_KEY = "key_settings_json"

# 点分路径 -> 关闭时写入的值
SWITCHES: Dict[str, Any] = {
    "draft_entryption_config.enable_all": False,
    "MaterialEncrypt.encry": False,
    "enable_encrypt_composite": False,
    "enable_import_encrypt_file_check": False,
    "upload_crypto_file_check_tag.enable": False,
}


def settings_path(user_data: Optional[str] = None) -> str:
    base = user_data or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~\\AppData\\Local")),
        "JianyingPro", "User Data")
    return os.path.join(base, "MMKV", "settings_json")


def sha_paths(user_data: Optional[str] = None):
    p = settings_path(user_data)
    return p, p + ".crc"


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def _dig(obj: Dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _bury(obj: Dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = obj
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def read_state(user_data: Optional[str] = None) -> Dict[str, Any]:
    p = settings_path(user_data)
    j = mmkv.get_json(p, SETTINGS_KEY)
    if j is None:
        return {k: None for k in SWITCHES}
    return {k: _dig(j, k) for k in SWITCHES}


def read_flag(user_data: Optional[str] = None) -> Optional[bool]:
    return read_state(user_data).get("draft_entryption_config.enable_all")


def is_fully_disabled(user_data: Optional[str] = None) -> bool:
    return all(v is False for v in read_state(user_data).values())


# --------------------------------------------------------------------------
# 只读锁
# --------------------------------------------------------------------------
def is_locked(user_data: Optional[str] = None) -> bool:
    p, c = sha_paths(user_data)
    out = []
    for f in (p, c):
        if os.path.isfile(f):
            out.append(not bool(os.stat(f).st_mode & stat.S_IWRITE))
    return all(out) if out else False


def _set_readonly(paths, ro: bool) -> None:
    for f in paths:
        if not os.path.isfile(f):
            continue
        mode = os.stat(f).st_mode
        os.chmod(f, (mode & ~stat.S_IWRITE) if ro else (mode | stat.S_IWRITE))


def lock(user_data: Optional[str] = None) -> bool:
    """给 settings_json(.crc) 加只读, 挡住剪映启动时的配置重置."""
    _set_readonly(sha_paths(user_data), True)
    return is_locked(user_data)


def unlock(user_data: Optional[str] = None) -> bool:
    _set_readonly(sha_paths(user_data), False)
    return not is_locked(user_data)


# --------------------------------------------------------------------------
# 主操作
# --------------------------------------------------------------------------
def apply(enabled: bool, user_data: Optional[str] = None,
          do_lock: bool = True) -> Dict[str, Any]:
    p = settings_path(user_data)
    if not os.path.isfile(p):
        raise FileNotFoundError(p)
    _set_readonly([p, p + ".crc"], False)      # 先解锁才能改
    bak = p + ".jyai.%s.bak" % time.strftime("%Y%m%d%H%M%S")
    shutil.copy2(p, bak)
    j = mmkv.get_json(p, SETTINGS_KEY)
    if j is None:
        raise ValueError("无法从 %s 解析 %s" % (p, SETTINGS_KEY))
    for dotted in SWITCHES:
        _bury(j, dotted, bool(enabled))
    _bury(j, "draft_entryption_config.mode", 0 if enabled else 2)
    mmkv.set_json(p, SETTINGS_KEY, j)
    locked = False
    if do_lock and not enabled:
        locked = lock(user_data)
    return {"settings": p, "backup": bak, "state": read_state(user_data),
            "crc_ok": mmkv.verify(p), "locked": locked}


def disable(user_data: Optional[str] = None, do_lock: bool = True) -> Dict[str, Any]:
    """关闭全部加解密开关; 默认同时加只读锁 (实测能扛住剪映启动重置)."""
    return apply(False, user_data, do_lock=do_lock)


def enable(user_data: Optional[str] = None) -> Dict[str, Any]:
    """解锁并恢复全部开关为 true."""
    return apply(True, user_data, do_lock=False)
