"""AI Creation Canvas 协作协议客户端.

逆向自 `Apps/<ver>/Resources/canvas_agent/runtime/app-server.mjs`
(protocol name: canvas-draft-collab/v2)

协议要点
--------
1. app-server 由 bun.exe 启动, 监听 127.0.0.1:<port>
2. 控制平面 `/api/agent-runtime/v1/...` 负责建实例 / 建会话
3. `resource` 必须 `{"id":<draftId>,"type":"canvas"}` 才能拿到 canvas 运行时
4. `/api/custom-agent/canvas-drafts/v2/sessions/open` 需要一份
   AES-256-GCM 加密的草稿根路径, 密钥由本地 token 经 HKDF 派生:

       key = HKDF-SHA256(ikm=token, salt="canvas-draft-binding-path/v1",
                         info=JSON(["v1", instanceId, draftId]))
       aad = JSON(["v1", requestId, instanceId, draftId])

   JSON 必须**无空格**(JS `JSON.stringify` 风格), 否则 AAD 不匹配解密失败。
5. 打开后拿到 `clientEpoch` / `generationId` / `revision` / `document`,
   即可用 `/api/custom-agent/dev/canvas-draft/debug-agent-op` 写入画布。

实测环境: 剪映 11.5.0.14471 / app-server 由 bun 1.3.14 承载
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import time
import uuid
from typing import Any, Dict, List, Optional

__all__ = [
    "CanvasError", "CanvasProtocol",
    "hkdf_sha256", "encrypt_draft_root", "b64url",
    "SALT_BINDING_PATH",
]

SALT_BINDING_PATH = b"canvas-draft-binding-path/v1"
ENC_VERSION = 1
ENC_ALGORITHM = "A256GCM"
ENC_KDF = "HKDF-SHA256"

# 画布节点类型 (app-server N$ 模块里的 S 枚举)
NODE_TYPES = {
    "text": "text", "image": "image", "video": "video", "audio": "audio",
    "film-preview": "film-preview", "group": "group", "script": "script",
    "character-group": "character-group", "asset-organizer": "asset-organizer",
    "storyboard-group": "storyboard-group",
    "edit-storyboard-group": "edit-storyboard-group",
}


class CanvasError(RuntimeError):
    """画布协议调用失败."""

    def __init__(self, message: str, status: int = 0, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


# --------------------------------------------------------------------------
# 密码学: 与 app-server 的 WX1/_X1/ZX1 保持一致
# --------------------------------------------------------------------------
def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF-SHA256 -> 定长密钥."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, t, i = b"", b"", 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([i]), hashlib.sha256).digest()
        okm += t
        i += 1
    return okm[:length]


def b64url(raw: bytes) -> str:
    """无填充 base64url (app-server 的 $X1)."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _js_json(obj: Any) -> bytes:
    """JS JSON.stringify 风格的紧凑 JSON (AAD/info 必须字节级一致)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode()


def _aes_gcm_encrypt(key: bytes, nonce: bytes, aad: bytes, plaintext: bytes):
    """AES-256-GCM 加密, 返回 (密文||16B tag)."""
    try:
        from Crypto.Cipher import AES                      # pycryptodome
        c = AES.new(key, AES.MODE_GCM, nonce=nonce)
        c.update(aad)
        ct, tag = c.encrypt_and_digest(plaintext)
        return ct + tag
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).encrypt(nonce, plaintext, aad)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, aad: bytes, blob: bytes) -> bytes:
    ct, tag = blob[:-16], blob[-16:]
    try:
        from Crypto.Cipher import AES
        c = AES.new(key, AES.MODE_GCM, nonce=nonce)
        c.update(aad)
        return c.decrypt_and_verify(ct, tag)
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, blob, aad)


def encrypt_draft_root(token: str, instance_id: str, draft_id: str,
                       request_id: str, draft_root: str,
                       nonce: Optional[bytes] = None) -> Dict[str, Any]:
    """把草稿根路径加密成 `/sessions/open` 要求的 encryptedDraftRoot. """
    info = _js_json(["v1", instance_id, draft_id])
    key = hkdf_sha256(token.encode("utf-8"), SALT_BINDING_PATH, info, 32)
    aad = _js_json(["v1", request_id, instance_id, draft_id])
    if nonce is None:
        nonce = os.urandom(12)                      # 96-bit, 服务端强制
    blob = _aes_gcm_encrypt(key, nonce, aad, draft_root.encode("utf-8"))
    return {
        "version": ENC_VERSION,
        "algorithm": ENC_ALGORITHM,
        "kdf": ENC_KDF,
        "nonce": b64url(nonce),
        "ciphertext": b64url(blob),
    }


def decrypt_draft_root(token: str, instance_id: str, draft_id: str,
                       request_id: str, payload: Dict[str, Any]) -> str:
    """反向解密 (自检用, 验证实现与服务端一致)."""
    info = _js_json(["v1", instance_id, draft_id])
    key = hkdf_sha256(token.encode("utf-8"), SALT_BINDING_PATH, info, 32)
    aad = _js_json(["v1", request_id, instance_id, draft_id])

    def _d(s: str) -> bytes:
        return base64.urlsafe_b64decode(s + "=" * ((4 - len(s) % 4) % 4))

    return _aes_gcm_decrypt(key, _d(payload["nonce"]), aad,
                            _d(payload["ciphertext"])).decode("utf-8")


# --------------------------------------------------------------------------
# 极简 HTTP (标准库, 无需 requests)
# --------------------------------------------------------------------------
class _Http:
    def __init__(self, host: str = "127.0.0.1", port: int = 54397):
        self.host, self.port = host, port

    def __call__(self, method: str, path: str, body: Any = None,
                 headers: Optional[Dict[str, str]] = None,
                 timeout: float = 30.0) -> Any:
        raw, hdr = self.raw(method, path, body, headers, timeout)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return raw

    def raw(self, method: str, path: str, body: Any = None,
            headers: Optional[Dict[str, str]] = None,
            timeout: float = 30.0):
        """返回 (body_text, status). 非 2xx 抛 CanvasError."""
        data = b""
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdr = {
            "Host": "%s:%d" % (self.host, self.port),
            "Connection": "close",
            "Accept": "application/json",
        }
        if data:
            hdr["Content-Type"] = "application/json"
            hdr["Content-Length"] = str(len(data))
        if headers:
            hdr.update(headers)

        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        try:
            req = "%s %s HTTP/1.1\r\n" % (method, path)
            req += "".join("%s: %s\r\n" % kv for kv in hdr.items())
            sock.sendall(req.encode("utf-8") + b"\r\n" + data)
            sock.settimeout(timeout)
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 64 * 1024 * 1024:
                    break

            head, _, rest = buf.partition(b"\r\n\r\n")
            head_txt = head.decode("utf-8", "replace")
            status = 0
            try:
                status = int(head_txt.split(" ")[1])
            except (IndexError, ValueError):
                pass

            if "transfer-encoding: chunked" in head_txt.lower():
                while not rest.endswith(b"\r\n0\r\n\r\n"):
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    rest += chunk
                out, pos = b"", 0
                while True:
                    nl = rest.find(b"\r\n", pos)
                    if nl < 0:
                        break
                    try:
                        size = int(rest[pos:nl].split(b";")[0], 16)
                    except ValueError:
                        break
                    if size == 0:
                        break
                    out += rest[nl + 2:nl + 2 + size]
                    pos = nl + 2 + size + 2
                payload = out
            else:
                clen = 0
                for line in head_txt.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        clen = int(line.split(":", 1)[1].strip())
                while len(rest) < clen:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    rest += chunk
                payload = rest[:clen] if clen else rest
        finally:
            sock.close()

        text = payload.decode("utf-8", "replace")
        if status >= 400:
            raise CanvasError("HTTP %d %s %s" % (status, method, path),
                              status=status, payload=text)
        return text, status


# --------------------------------------------------------------------------
# 画布协议客户端
# --------------------------------------------------------------------------
class CanvasProtocol:
    """AI Creation Canvas (无限画布) 协作客户端.

    典型用法::

        cp = CanvasProtocol(port=54397)
        inst = cp.resolve_canvas_instance(draft_id, draft_root)
        sess = cp.open(draft_id, draft_root)          # 需要 token
        cp.add_text(sess, "标题")
        cp.update_text(sess, node_id, "正文")
        cp.remove_node(sess, node_id)
    """

    PLUGIN_ID = "custom-agent"

    def __init__(self, port: int = 54397, host: str = "127.0.0.1",
                 token: Optional[str] = None, canvas_id: str = "canvas-main"):
        self.http = _Http(host, port)
        # 本地 server token: 环境变量未设时服务端只校验"非空"
        self.token = token or os.urandom(32).hex()
        self.canvas_id = canvas_id
        self.instance_id: Optional[str] = None
        self.session_id: Optional[str] = None
        self.client_epoch: Optional[str] = None
        self.generation_id: Optional[str] = None
        self.revision: int = 0
        self.document: Dict[str, Any] = {}

    # ---------------- 控制平面 ----------------
    def _binding_headers(self) -> Dict[str, str]:
        h = {}
        if self.instance_id:
            h["x-agent-runtime-instance-id"] = self.instance_id
        if self.session_id:
            h["x-agent-runtime-session-id"] = self.session_id
        return h

    def health(self) -> Dict[str, Any]:
        raw, _ = self.http.raw("GET", "/api/agent-runtime/v1/host/health")
        return json.loads(raw)

    def set_host_app_context(self, device_id: str, app_id: str = "3704",
                             app_version: str = "11.5.0.14471",
                             region: str = "CN", language: str = "zh-Hans",
                             platform: str = "windows", channel: str = "jianying",
                             app_sdk_version: str = "22.2.0.99999-revision") -> Dict[str, Any]:
        """同步宿主 App 上下文 (tdid/pf/appvr 等; 缺了 broker 会 500)."""
        ctx = {
            "schema": "agent-runtime.host-app-context.v1",
            "clientInstanceId": "custom-agent-dev-client",
            "region": region, "language": language, "platformCode": platform,
            "channel": channel, "appVersion": app_version, "appId": app_id,
            "deviceId": device_id, "appSdkVersion": app_sdk_version,
        }
        raw, _ = self.http.raw("PUT", "/api/agent-runtime/v1/host/app-context", ctx)
        return json.loads(raw)

    def resolve_canvas_instance(self, draft_id: str, keep_alive: bool = True) -> str:
        """为某草稿解析 canvas 运行时实例 (resource.type 必须是 canvas)."""
        body = {
            "pluginId": self.PLUGIN_ID,
            "resource": {"id": draft_id, "type": "canvas"},
        }
        if keep_alive:
            body["retention"] = {"mode": "keep_alive", "idleTtlMs": 86400000}
        raw, _ = self.http.raw("POST", "/api/agent-runtime/v1/instances:resolve", body)
        data = json.loads(raw)
        self.instance_id = data["instance"]["instanceId"]
        return self.instance_id

    def create_session(self, title: Optional[str] = None) -> str:
        body = {"title": title} if title else {}
        raw, _ = self.http.raw(
            "POST",
            "/api/agent-runtime/v1/instances/%s/sessions" % self.instance_id, body)
        data = json.loads(raw)
        self.session_id = data.get("sessionId") or data.get("session", {}).get("sessionId")
        return self.session_id

    def list_sessions(self) -> List[Dict[str, Any]]:
        raw, _ = self.http.raw(
            "GET",
            "/api/agent-runtime/v1/instances/%s/sessions" % self.instance_id)
        return json.loads(raw).get("items", [])

    # ---------------- 画布会话 ----------------
    def open(self, draft_id: str, draft_root: str,
             request_id: Optional[str] = None) -> Dict[str, Any]:
        """打开画布会话, 返回 snapshot (含 clientEpoch/generationId/document)."""
        if not self.instance_id:
            self.resolve_canvas_instance(draft_id)
        request_id = request_id or ("req-%s" % uuid.uuid4())
        payload = encrypt_draft_root(self.token, self.instance_id, draft_id,
                                     request_id, draft_root)
        body = {
            "requestId": request_id,
            "instanceId": self.instance_id,
            "draftId": draft_id,
            "canvasId": self.canvas_id,
            "encryptedDraftRoot": payload,
        }
        headers = dict(self._binding_headers())
        headers["x-custom-agent-token"] = self.token
        raw, _ = self.http.raw(
            "POST", "/api/custom-agent/canvas-drafts/v2/sessions/open", body, headers)
        data = json.loads(raw)
        self.client_epoch = data.get("clientEpoch")
        snap = data.get("snapshot", {})
        self.generation_id = snap.get("generationId")
        self.revision = snap.get("revision", 0)
        self.document = snap.get("document", {})
        self._last_open = data
        return data

    def reopen(self, draft_id: str, draft_root: str) -> Dict[str, Any]:
        """重新拉一次快照 (拿到最新 revision/document)."""
        return self.open(draft_id, draft_root)

    def nodes(self) -> List[Dict[str, Any]]:
        return list(self.document.get("nodes", []))

    # ---------------- Agent 操作注入 ----------------
    def submit_operation(self, draft_id: str, operation: Dict[str, Any],
                         generation_id: Optional[str] = None,
                         submit_delay_ms: int = 0) -> Dict[str, Any]:
        """向画布提交一次 agent 操作.

        operation 支持 (app-server 白名单, 见 s64):
            {"type":"add_blank_text"}                     新建空文本节点
            {"type":"add_blank_text","nodeId":"my-id"}    指定 nodeId
            {"type":"update_text","nodeId":..,"text":..}  改文本
            {"type":"remove_node","nodeId":..}            删节点
        """
        body = {
            "instanceId": self.instance_id,
            "draftId": draft_id,
            "canvasId": self.canvas_id,
            "generationId": generation_id or self.generation_id,
            "operation": operation,
        }
        if submit_delay_ms:
            body["submitDelayMs"] = submit_delay_ms
        raw, _ = self.http.raw(
            "POST", "/api/custom-agent/dev/canvas-draft/debug-agent-op", body,
            self._binding_headers())
        data = json.loads(raw)
        fin = data.get("finalReceipt") or {}
        if fin.get("revision"):
            self.revision = fin["revision"]
        return data

    # --- 便捷封装 ---
    def add_text(self, draft_id: str, text: str = "", node_id: Optional[str] = None):
        op = {"type": "add_blank_text"}
        if node_id:
            op["nodeId"] = node_id
        res = self.submit_operation(draft_id, op)
        nid = res.get("nodeId")
        if text:
            self.update_text(draft_id, nid, text)
        return nid

    def update_text(self, draft_id: str, node_id: str, text: str):
        return self.submit_operation(
            draft_id, {"type": "update_text", "nodeId": node_id, "text": text})

    def remove_node(self, draft_id: str, node_id: str):
        return self.submit_operation(
            draft_id, {"type": "remove_node", "nodeId": node_id})
