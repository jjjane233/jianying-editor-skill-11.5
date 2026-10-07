"""剪映加密密钥库 crypto_key_store.dat 的读写实现.

实测格式 (11.3.0.14362, 文件位于 User Data/Config/crypto_key_store.dat)
-----------------------------------------------------------------------
    外层:  [u32 LE  zlib 流长度] + [zlib(deflate) 流]
    deflate 解开后是一段 tag-length 结构:
        [u32   total_size]
        [u8    版本/tag, 实测 0x03]
        "keys\0"
        [u32   body_size]
        body: 若干条目, 每条:
            [u8 tag] [cstring 条目名] [u32 payload_size] [payload]
        payload 内是 KV 组:
            [u8 tag] [cstring 字段名] [u32 长度] [长度字节的值]

    条目名的原始语义是“密钥编号”(实测出现过 "0" "1" "3" "5" "b" 等),
    但真正用来索引的是 KV 里的 uri 字段 —— 剪映按 uri 反查密钥.

    字段:
        cipher_key        16 字节  AES-128 密钥
        cipher_type       1 字节   算法/模式选择
        optional_context  1 字节   附加上下文
        uri               变长     "\x7c\x68" 前缀 + 相对路径 + "\0"
                                  (|h/Cache/... , 对应 ContentStore 的缓存路径)

解析实现上不依赖各条目的 tag 取值(实测 tag 不固定),
改为按字段名精确定位, 兼容后续小版本把 tag 换掉的情况.
"""
from __future__ import annotations
import os, struct, zlib
from typing import Any, Dict, List, Optional

FIELDS = ("cipher_key", "cipher_type", "optional_context", "uri")

# 实测 tag 取值 (仅作参考, 解析不依赖)
TAG_ENTRY_ROOT = 0x03
TAG_ENTRY = 0x03


# --------------------------------------------------------------------------
# 基础读取
# --------------------------------------------------------------------------
def _u32(buf: bytes, pos: int) -> int:
    return struct.unpack_from("<I", buf, pos)[0]


def _cstr_end(buf: bytes, start: int) -> int:
    return buf.index(b"\x00", start)


def decompress(raw: bytes) -> bytes:
    """拆掉长度头并 zlib 解压. 容错尝试多个起始偏移."""
    for off in (4, 0, 2, 8):
        try:
            return zlib.decompress(raw[off:])
        except zlib.error:
            continue
    raise ValueError("crypto_key_store 不是已知的 zlib 容器格式")


def _scan_field(buf: bytes, name: str, pos: int) -> Optional[Dict[str, Any]]:
    """在 buf[pos:] 里找 <name>\\0, 读出紧随的 u32 长度和值."""
    pat = name.encode() + b"\x00"
    i = buf.find(pat, pos)
    if i < 0:
        return None
    lp = i + len(pat)
    if lp + 4 > len(buf):
        return None
    size = _u32(buf, lp)
    if size > 4096:
        return None
    val = buf[lp + 4:lp + 4 + size]
    if len(val) != size:
        return None
    return {"name": name, "offset": i, "size": size, "value": val,
            "tail": i + len(pat) + 4}


def parse(raw: bytes) -> List[Dict[str, Any]]:
    """解析出全部密钥条目.

    返回 [{'name': 条目名, 'cipher_key': bytes, 'cipher_type': int,
            'optional_context': int, 'uri': str, 'raw': {...}}, ...]
    """
    buf = decompress(raw)

    # 外层头
    total = _u32(buf, 0)
    p = 4 + 1                                    # 跳过 total + 版本 tag
    e = _cstr_end(buf, p)
    root = buf[p:e].decode("utf-8", "replace")   # 实测 "keys"
    p = e + 1
    body = _u32(buf, p)
    p += 4
    body_end = p + body

    # 定位所有条目: 每个 cipher_key 字段就是一条
    entries: List[Dict[str, Any]] = []
    cur = p
    while cur < body_end:
        f = _scan_field(buf, "cipher_key", cur)
        if not f:
            break
        ent: Dict[str, Any] = {"name": "", "cipher_key": f["value"], "raw": {}}
        # 布局: [u8 entry tag][cstring 条目名][u32 payload 大小][u8 KV tag]"cipher_key"
        # 故条目名 NUL 位于 cipher_key 之前 5 字节, 再往前回溯到上一个 NUL
        ne = f["offset"] - 6
        if 0 <= ne < len(buf) and buf[ne] == 0:
            ns = buf.rfind(b"\x00", max(p, ne - 128), ne)
            raw_name = buf[ns + 1:ne] if ns >= 0 else b""
            # 条目名前面还有 1 字节 entry tag(0x03), 一并去掉
            ent["name"] = bytes(b for b in raw_name if 32 <= b < 127).decode("utf-8", "replace")
        else:
            ent["name"] = ""
        for name in FIELDS:
            g = _scan_field(buf, name, f["offset"])
            if g:
                ent["raw"][name] = g["value"]
        ent["cipher_type"] = (ent["raw"].get("cipher_type") or b"")[:1]
        ent["cipher_type"] = ent["cipher_type"][0] if ent["cipher_type"] else None
        oc = (ent["raw"].get("optional_context") or b"")
        ent["optional_context"] = oc[0] if oc else None
        uri = (ent["raw"].get("uri") or b"")
        ent["uri"] = uri.rstrip(b"\x00").decode("utf-8", "replace")
        ent["cipher_key_hex"] = ent["cipher_key"].hex()
        entries.append(ent)
        cur = f["tail"]

    return entries


def load(path: str) -> List[Dict[str, Any]]:
    return parse(open(path, "rb").read())


def keys_of(path: str) -> List[str]:
    """只取 hex 形式的 AES 密钥."""
    return [e["cipher_key_hex"] for e in load(path)]


def by_uri(path: str, uri: str) -> Optional[Dict[str, Any]]:
    for e in load(path):
        if e["uri"] == uri or e["uri"].endswith(uri.split("/")[-1]):
            return e
    return None


# --------------------------------------------------------------------------
# 写回
# --------------------------------------------------------------------------
def _kv(tag: int, name: str, value: bytes) -> bytes:
    return bytes([tag]) + name.encode() + b"\x00" + struct.pack("<I", len(value)) + value


def build(entries: List[Dict[str, Any]]) -> bytes:
    """把条目列表编回 crypto_key_store.dat 字节 (含 zlib 压缩与长度头)."""
    body = bytearray()
    for i, e in enumerate(entries):
        payload = bytearray()
        payload += _kv(0x05, "cipher_key", e["cipher_key"])
        payload += _kv(0x10, "cipher_type", bytes([e.get("cipher_type") or 2]))
        payload += _kv(0x02, "optional_context", bytes([e.get("optional_context") or 0]))
        uri = e["uri"].encode() + b"\x00"
        payload += _kv(0x02, "uri", uri)
        name = (e.get("name") or str(i)).encode()
        body += bytes([TAG_ENTRY]) + name + b"\x00" + struct.pack("<I", len(payload)) + bytes(payload)

    inner = bytearray()
    inner += b"keys\x00"
    inner += struct.pack("<I", len(body))
    inner += body
    head = struct.pack("<I", len(inner) + 1) + bytes([TAG_ENTRY_ROOT])
    plain = head + inner

    comp = zlib.compress(bytes(plain), 9)
    return struct.pack("<I", len(comp)) + comp


def save(path: str, entries: List[Dict[str, Any]]) -> str:
    data = build(entries)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return path


def add_material_key(path: str, uri: str, key16: bytes,
                     cipher_type: int = 2, optional_context: int = 0,
                     name: Optional[str] = None) -> Dict[str, Any]:
    """新增 / 覆盖一条素材密钥 (按 uri 匹配)."""
    entries = load(path)
    for e in entries:
        if e["uri"] == uri:
            e["cipher_key"] = key16
            e["cipher_type"] = cipher_type
            e["optional_context"] = optional_context
            e["cipher_key_hex"] = key16.hex()
            save(path, entries)
            return e
    idx = 0
    names = {e["name"] for e in entries}
    while str(idx) in names:
        idx += 1
    ent = {"name": name or str(idx), "cipher_key": key16,
           "cipher_type": cipher_type, "optional_context": optional_context,
           "uri": uri, "cipher_key_hex": key16.hex(), "raw": {}}
    entries.append(ent)
    save(path, entries)
    return ent
