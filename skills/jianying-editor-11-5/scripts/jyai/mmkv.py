"""剪映 MMKV 配置读写 (User Data/MMKV/*).

实测格式 (11.3.0.14362 / 11.5.0.14471)
--------------------------------------
数据文件 <name> (settings_json 定长 16 MiB):
    [u32 头字段][u32 0x07FFFFFF]                       <- 8 字节头
    [u8 keyLen(varint)][key][varint sizeA][varint sizeB][value]
    ... 继续追加, 同 key 后写覆盖先写, 末尾以 0 填充

    注意: 头 4 字节在不同版本里含义不同 —— 11.3 是已用字节数,
          11.5 重写后变成 4(一个版本号). 所以解析**不能依赖它**,
          必须按记录链一路读到 0 终止符为止.

校验文件 <name>.crc (4096 字节):
    +0x00 u32  crc32( dat[4 : 8 + 头字段] )  —— 实测就是 crc32(dat[4:end])
    +0x04 u32  3
    +0x08 u32  记录条数
    +0x1c u32  与头字段同值
不刷新 .crc 会导致 MMKV 判损坏并整份回滚, 所以写入后必须同步.
"""
from __future__ import annotations
import json, os, struct, zlib
from typing import Any, Dict, List, Optional, Tuple


# --------------------------------------------------------------------------
# varint
# --------------------------------------------------------------------------
def _varint(buf: bytes, i: int) -> Tuple[int, int]:
    r = s = 0
    while True:
        b = buf[i]; i += 1
        r |= (b & 0x7F) << s
        if not (b & 0x80):
            return r, i
        s += 7


def _enc_varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


# --------------------------------------------------------------------------
# 记录层
# --------------------------------------------------------------------------
def _scan(buf: bytes, start: int = 8) -> Tuple[List[Tuple[str, bytes]], int]:
    """从 start 起按记录链解析, 返回 (records, 数据末端偏移)."""
    out: List[Tuple[str, bytes]] = []
    i, n = start, len(buf)
    while i < n:
        if buf[i] == 0:
            break
        try:
            klen, j = _varint(buf, i)
            if not (0 < klen <= 4096) or j + klen > n:
                break
            key = buf[j:j + klen].decode("utf-8", "replace"); j += klen
            sa, j = _varint(buf, j)
            sb, j = _varint(buf, j)
            size = sb if sb <= sa else sa
            if size < 0 or j + size > n:
                break
            out.append((key, buf[j:j + size]))
            i = j + size
        except Exception:
            break
    return out, i


def iter_records(path: str) -> List[Tuple[str, bytes]]:
    """按物理顺序返回全部记录 (含被后来者覆盖的历史版本)."""
    with open(path, "rb") as f:
        buf = f.read()
    return _scan(buf)[0]


def read_records(path: str) -> Dict[str, bytes]:
    """取每个 key 的最新值."""
    out: Dict[str, bytes] = {}
    for k, v in _scan(open(path, "rb").read())[0]:
        out[k] = v
    return out


# --------------------------------------------------------------------------
# 值编解码
# --------------------------------------------------------------------------
def decode(value: bytes) -> Any:
    try:
        s = value.decode("utf-8")
    except UnicodeDecodeError:
        return value
    s = s.rstrip("\x00")
    try:
        return json.loads(s)
    except Exception:
        return s


def encode(obj: Any) -> bytes:
    if isinstance(obj, (dict, list)):
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if isinstance(obj, str):
        return obj.encode("utf-8")
    if isinstance(obj, bool):
        return b"\x01" if obj else b"\x00"
    if isinstance(obj, bytes):
        return obj
    return str(obj).encode()


def get_json(path: str, key: str, default: Any = None) -> Any:
    raw = read_records(path).get(key)
    if raw is None:
        return default
    try:
        return json.loads(raw.decode("utf-8").rstrip("\x00"))
    except Exception:
        return default


def get(path: str, key: str, default: Any = None) -> Any:
    raw = read_records(path).get(key)
    return default if raw is None else decode(raw)


# --------------------------------------------------------------------------
# 写入
# --------------------------------------------------------------------------
def set_value(path: str, key: str, value: bytes) -> str:
    """追加一条记录覆盖同名 key (MMKV 语义: 后写覆盖), 并同步 .crc."""
    with open(path, "rb") as f:
        old = f.read()
    header = old[:8]
    recs, end = _scan(old)
    kb = key.encode("utf-8")
    rec = _enc_varint(len(kb)) + kb + _enc_varint(len(value)) + _enc_varint(len(value)) + value
    data = header + old[8:end] + rec
    cap = max(len(old), ((len(data) // 4096) + 1) * 4096)
    out = bytearray(cap)
    out[:len(data)] = data
    # 头字段与已用长度保持一致 (11.3 语义); 11.5 会自行重写
    struct.pack_into("<I", out, 0, len(old[8:end]) + len(rec))
    with open(path, "wb") as f:
        f.write(bytes(out))
    sync_crc(path)
    return path


def set_json(path: str, key: str, obj: Any) -> str:
    return set_value(path, key, encode(obj))


# --------------------------------------------------------------------------
# .crc 校验
# --------------------------------------------------------------------------
def sync_crc(path: str) -> str:
    """按 <path> 当前内容重算并写回 <path>.crc."""
    dat = open(path, "rb").read()
    _, end = _scan(dat)
    head = struct.unpack_from("<I", dat, 0)[0]
    # 实测校验范围 = dat[4 : 4 + 头字段]  (11.3 头=已用长度, 11.5 头=4)
    if not (0 < head <= len(dat) - 8):
        head = end - 4
        buf = bytearray(dat); struct.pack_into("<I", buf, 0, head); dat = bytes(buf)
    stop = 4 + head
    crc = zlib.crc32(dat[4:stop]) & 0xFFFFFFFF
    nrec = len(_scan(dat)[0])
    cf = path + ".crc"
    cb = bytearray(open(cf, "rb").read()) if os.path.isfile(cf) else bytearray(4096)
    if len(cb) < 0x24:
        cb.extend(b"\x00" * (0x24 - len(cb)))
    struct.pack_into("<I", cb, 0x00, crc)
    struct.pack_into("<I", cb, 0x04, 3)
    struct.pack_into("<I", cb, 0x08, nrec)
    struct.pack_into("<I", cb, 0x1c, struct.unpack_from("<I", dat, 0)[0])
    with open(cf, "wb") as f:
        f.write(bytes(cb))
    return cf


def verify(path: str) -> bool:
    dat = open(path, "rb").read()
    cf = path + ".crc"
    if not os.path.isfile(cf):
        return False
    head = struct.unpack_from("<I", dat, 0)[0]
    stop = 4 + head if 0 < head <= len(dat) - 8 else _scan(dat)[1]
    stored = struct.unpack_from("<I", open(cf, "rb").read(), 0)[0]
    return (zlib.crc32(dat[4:stop]) & 0xFFFFFFFF) == stored
