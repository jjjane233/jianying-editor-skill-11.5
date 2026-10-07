"""官方 CustomAgent 运行时通道的离线回归测试 (不依赖剪映/网络).

覆盖:
  * 协议常量 (指令白名单 / 内置 Agent / 素材扩展名)
  * 请求构造: 鉴权头 + runtime 绑定头 + JSON body
  * 错误分类: HTTP 状态 / error.code 提取
  * workspace 素材登记: 目录展开 / 扩展名过滤 / entryType 只能是 file
  * 命令封装: SubmitTask / AdvanceRun / createWorkspaceTask 的字段

实测依据 (11.5.0.14471, 2026-10-01/02):
  GET  /api/agent-runtime/v1/host/health          -> 200 {"ok":true,...}
  POST /api/agent-runtime/v1/instances:resolve    -> 201 {"created":true,"instance":{...}}
  GET  /api/custom-agent/host-capabilities/manifest
       无绑定      -> 400 runtime_binding_required
       仅 instance -> 400 invalid_runtime_binding
       两者都有    -> 401 invalid_host_capability_token  (该路由只认进程级 grant)
"""
import json
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "jianying-editor-11-5", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from jyai import agentruntime as ar      # noqa: E402


class _FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Calls:
    """记录 request() 收到的 header/body, 并按队列返回响应."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.seen = []

    def __call__(self, req, timeout=None):
        self.seen.append({
            "url": req.full_url,
            "method": req.get_method(),
            "headers": {k.lower(): v for k, v in req.header_items()},
            "body": json.loads(req.data.decode("utf-8")) if req.data else None,
        })
        status, payload = self.replies.pop(0)
        return _FakeResponse(status, payload)


def _client(replies, **kw):
    srv = ar.LiveServer(port=12345, token="tok-abc", pid=999)
    c = ar.AgentRuntimeClient(srv, timeout=5, **kw)
    calls = _Calls(replies)
    c._patch_target = calls
    return c, calls


class ProtocolTests(unittest.TestCase):
    def test_command_kinds_cover_core_lifecycle(self):
        for k in ("SubmitTask", "AdvanceRun", "OpenWorkspaceRun",
                  "SendUserMessage", "RunStage", "CancelRun"):
            self.assertIn(k, ar.COMMAND_KINDS)

    def test_runtime_modes_match_live_snapshot(self):
        # SubmitTask 响应里实测的 4 个内置 Agent
        self.assertEqual(
            set(ar.RUNTIME_MODES),
            {"v2-director-agent", "v2-script-agent",
             "v2-editing-agent", "v2-review-agent"})

    def test_media_exts_contains_video_audio_image(self):
        for e in (".mp4", ".mp3", ".png", ".srt"):
            self.assertIn(e, ar.MEDIA_EXTS)

    def test_live_server_base(self):
        s = ar.LiveServer(port=60274, token="t", pid=1)
        self.assertEqual(s.base, "http://127.0.0.1:60274")


class ErrorMappingTests(unittest.TestCase):
    def test_error_code_from_payload(self):
        e = ar.AgentRuntimeError("m", 400, {"error": {"code": "runtime_binding_required"}})
        self.assertEqual(e.status, 400)
        self.assertEqual(e.code, "runtime_binding_required")

    def test_error_code_explicit_wins(self):
        e = ar.AgentRuntimeError("m", 500, {"error": {"code": "x"}}, code="y")
        self.assertEqual(e.code, "y")

    def test_error_code_empty_when_no_payload(self):
        self.assertEqual(ar.AgentRuntimeError("m").code, "")


class RequestConstructionTests(unittest.TestCase):
    def test_call_raises_with_server_message(self):
        srv = ar.LiveServer(port=1, token="t")
        c = ar.AgentRuntimeClient(srv, timeout=2)
        with mock.patch("urllib.request.urlopen",
                        return_value=_FakeResponse(400, {"error": {
                            "code": "runtime_binding_required",
                            "message": "need binding"}})):
            with self.assertRaises(ar.AgentRuntimeError) as ctx:
                c.call("GET", "/x")
        self.assertEqual(ctx.exception.code, "runtime_binding_required")
        self.assertIn("need binding", str(ctx.exception))

    def test_headers_and_binding(self):
        c, calls = _client([(200, {"ok": True})])
        c.instance_id = "inst-1"
        c.session_id = "sess-1"
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            c.health()
        h = calls.seen[0]["headers"]
        self.assertEqual(h["x-custom-agent-token"], "tok-abc")
        # health 是 bind=False, 不该带运行时绑定头
        self.assertNotIn("x-agent-runtime-instance-id", h)

    def test_binding_headers_present_when_bound(self):
        c, calls = _client([(200, {})])
        c.instance_id = "inst-1"
        c.session_id = "sess-1"
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            c.call("GET", "/whatever")
        h = calls.seen[0]["headers"]
        self.assertEqual(h["x-agent-runtime-instance-id"], "inst-1")
        self.assertEqual(h["x-agent-runtime-session-id"], "sess-1")

    def test_resolve_instance_body_and_parse(self):
        c, calls = _client([(201, {"created": True,
                                   "instance": {"instanceId": "instance-xyz"}})])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            iid = c.resolve_instance("jyai-test")
        self.assertEqual(iid, "instance-xyz")
        body = calls.seen[0]["body"]
        self.assertEqual(body["pluginId"], "custom-agent")
        self.assertEqual(body["resource"], {"type": "canvas", "id": "jyai-test"})
        self.assertEqual(body["retention"], {"mode": "keep_alive"})

    def test_submit_task_fields(self):
        c, calls = _client([(200, {"resultSummary": {"runId": "r1"}})])
        c.instance_id = "i1"
        c.session_id = "s1"
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            c.submit_task("剪个视频", agent_id="v2-director-agent")
        cmd = calls.seen[0]["body"]["command"]
        self.assertEqual(cmd["kind"], "SubmitTask")
        self.assertEqual(cmd["taskDescription"], "剪个视频")
        self.assertEqual(cmd["agentId"], "v2-director-agent")
        self.assertTrue(calls.seen[0]["body"]["compact"])

    def test_advance_and_send_message_kinds(self):
        c, calls = _client([(200, {}), (200, {})])
        c.instance_id = "i1"
        c.session_id = "s1"
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            c.advance("r1", reason="test")
            c.send_message("改短一点", "r1")
        self.assertEqual(calls.seen[0]["body"]["command"]["kind"], "AdvanceRun")
        self.assertEqual(calls.seen[1]["body"]["command"]["kind"], "SendUserMessage")
        self.assertEqual(calls.seen[1]["body"]["command"]["message"], "改短一点")

    def test_extract_run_id_variants(self):
        self.assertEqual(ar.AgentRuntimeClient.extract_run_id(
            {"resultSummary": {"runId": "a"}}), "a")
        self.assertEqual(ar.AgentRuntimeClient.extract_run_id(
            {"summary": {"activeRunId": "b"}}), "b")
        self.assertEqual(ar.AgentRuntimeClient.extract_run_id(
            {"result": {"runId": "c"}}), "c")
        self.assertEqual(ar.AgentRuntimeClient.extract_run_id({}), "")


class WorkspaceTests(unittest.TestCase):
    def _mk(self, tmp):
        os.makedirs(tmp, exist_ok=True)
        with open(os.path.join(tmp, "a.mp4"), "wb") as f:
            f.write(b"x" * 10)
        with open(os.path.join(tmp, "b.txt"), "w", encoding="utf-8") as f:
            f.write("hi")
        with open(os.path.join(tmp, "skip.exe"), "wb") as f:
            f.write(b"z")
        sub = os.path.join(tmp, "sub")
        os.makedirs(sub, exist_ok=True)
        with open(os.path.join(sub, "c.png"), "wb") as f:
            f.write(b"p")
        return tmp

    def test_register_expands_dir_and_filters_exts(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="jyai_ar_test_")
        self._mk(tmp)
        c, calls = _client([(200, {"assets": [{"assetId": "a1", "kind": "video",
                                               "title": "a.mp4", "ref": "local-input://a1"}]})])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            assets = c.register_local_paths(tmp, workspace_path="W")
        body = calls.seen[0]["body"]
        self.assertEqual(body["source"], "server-picker")
        self.assertEqual(body["workspacePath"], "W")
        self.assertFalse(body["publishToAssetManagement"])
        names = sorted(os.path.basename(e["path"]) for e in body["entries"])
        self.assertEqual(names, ["a.mp4", "b.txt", "c.png"])       # skip.exe 被过滤
        for e in body["entries"]:
            self.assertEqual(e["entryType"], "file")               # folder 会失败
            self.assertIn("sizeBytes", e)
        self.assertTrue(calls.seen[0]["url"].endswith("/local-inputs/register-paths"))
        self.assertEqual(assets[0]["assetId"], "a1")

    def test_register_single_file(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="jyai_ar_test_")
        fp = os.path.join(tmp, "only.mp4")
        with open(fp, "wb") as f:
            f.write(b"x")
        c, calls = _client([(200, {"assets": []})])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            c.register_local_paths(fp, workspace_path="W")
        self.assertEqual(len(calls.seen[0]["body"]["entries"]), 1)

    def test_register_raises_when_nothing_usable(self):
        import tempfile
        tmp = tempfile.mkdtemp(prefix="jyai_ar_test_")
        c, _ = _client([(200, {})])
        with self.assertRaises(ar.AgentRuntimeError):
            c.register_local_paths(tmp, workspace_path="W")

    def test_prepare_workspace(self):
        c, calls = _client([(200, {"operation": "task.workspace.prepare",
                                   "workspacePath": "C:/tmp/ws"})])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            wp = c.prepare_workspace()
        self.assertEqual(wp, "C:/tmp/ws")
        self.assertTrue(calls.seen[0]["url"].endswith("/tasks/workspace/prepare"))

    def test_create_workspace_task(self):
        c, calls = _client([(200, {"task": {"taskId": "t1", "runId": "r1",
                                           "status": "submitted"}})])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            out = c.create_workspace_task(
                "剪成竖屏", [{"ref": "local-input://x", "assetId": "x",
                              "title": "a.mp4", "kind": "video"}],
                workspace_path="W", material_directory="D")
        body = calls.seen[0]["body"]
        self.assertEqual(body["agentId"], "v2-director-agent")
        self.assertEqual(body["taskText"], "剪成竖屏")
        self.assertEqual(body["assetRefs"][0]["assetId"], "x")
        self.assertEqual(body["workspacePath"], "W")
        self.assertEqual(body["materialDirectory"], "D")
        self.assertTrue(body["directOutput"])
        self.assertTrue(calls.seen[0]["url"].endswith("/tasks/workspace"))
        self.assertEqual(out["task"]["runId"], "r1")
        self.assertEqual(c.last_run_id, "r1")
        self.assertEqual(c.last_task_id, "t1")


class SkillsSurfaceTests(unittest.TestCase):
    def test_skills_and_processors_parse(self):
        c, calls = _client([
            (200, {"skills": [{"skillId": "s1"}]}),
            (200, {"processors": [{"processorId": "p1"}]}),
            (200, {"servers": []}),
        ])
        with mock.patch("urllib.request.urlopen", side_effect=calls):
            self.assertEqual(c.skills()[0]["skillId"], "s1")
            self.assertEqual(c.processors()[0]["processorId"], "p1")
        # 这三个接口是 requiresRuntimeBinding:!1, 用 bind=False
        for s in calls.seen:
            self.assertNotIn("x-agent-runtime-instance-id", s["headers"])


if __name__ == "__main__":
    unittest.main()
