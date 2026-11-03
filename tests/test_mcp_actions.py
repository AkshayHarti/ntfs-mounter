import io
import json

from ntfsmnt.mcp import call_tool, handle, serve
from ntfsmnt.mcp_client import ControllerError
from tests.web_helpers import make_volume


def volumes():
    return [make_volume(label="HD1")]


def text_of(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def body_of(result):
    return json.loads(result["content"][0]["text"])


class FakeController:
    def __init__(self, *, post=None, get=None, delete=None):
        self.posts = []
        self.gets = []
        self.deletes = []
        self._post = post or (lambda path, body, extra: {"ok": True})
        self._get = get or (lambda path: {"ok": True})
        self._delete = delete or (lambda path: {"ok": True})

    def post(self, path, body=None, extra=None):
        self.posts.append({"path": path, "body": body, "extra": extra})
        return self._post(path, body, extra)

    def get(self, path):
        self.gets.append(path)
        return self._get(path)

    def delete(self, path):
        self.deletes.append(path)
        return self._delete(path)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        self.now += 1
        return self.now


def test_initialize_instructions_mention_grants_and_approval():
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    instructions = reply["result"]["instructions"].lower()
    assert "grant" in instructions
    assert "approval" in instructions or "approve" in instructions
    assert "recover" in instructions


def test_initialize_does_not_claim_read_only():
    reply = handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "read-only tools" not in reply["result"]["instructions"].lower()


def test_request_grant_polls_until_approved():
    responses = iter([{"request_id": "r_1", "status": "pending",
                       "wait_seconds": 90},
                      {"request_id": "r_1", "status": "pending"},
                      {"request_id": "r_1", "status": "approved",
                       "grant_id": "g_1"}])
    fake = FakeController(
        post=lambda p, b, e: next(responses),
        get=lambda p: next(responses),
    )

    result = call_tool("request_grant",
                       {"volume": "HD1", "actions": ["mount"]},
                       controller=fake, client_name="Claude Desktop",
                       sleep=lambda s: None, clock=Clock())

    body = json.loads(result["content"][0]["text"])
    assert result["isError"] is False
    assert body == {"request_id": "r_1", "status": "approved", "grant_id": "g_1"}
    assert "approval_url" not in result["content"][0]["text"]


def test_request_grant_timeout_returns_timeout():
    fake = FakeController(
        post=lambda p, b, e: {"request_id": "r_1", "status": "pending",
                              "wait_seconds": 0})

    result = call_tool("request_grant",
                       {"volume": "HD1", "actions": ["mount"]},
                       controller=fake, sleep=lambda s: None, clock=Clock())

    body = body_of(result)
    assert body["status"] == "timeout"
    assert body["request_id"] == "r_1"


def test_request_grant_rejects_destructive_actions_before_any_http():
    fake = FakeController()

    result = call_tool("request_grant",
                       {"volume": "HD1", "actions": ["remove_hiberfile"]},
                       controller=fake)

    assert result["isError"] is True
    assert body_of(result)["error"]["code"] == "manual_required"
    assert "ntfsmnt mount --remove-hiberfile" in body_of(result)["error"]["next_step"]
    assert fake.posts == []


def test_request_grant_recover_is_refused():
    fake = FakeController()

    result = call_tool("request_grant",
                       {"volume": "HD1", "actions": ["recover"]},
                       controller=fake)

    assert result["isError"] is True
    assert body_of(result)["error"]["code"] == "manual_required"
    assert fake.posts == []


def test_mount_sends_the_grant_header_and_needs_no_flags():
    fake = FakeController(
        post=lambda p, b, e: {"ok": True, "message": "HD1 is writable."})

    result = call_tool("mount_volume",
                       {"volume": "HD1", "grant_id": "g_1"},
                       controller=fake, collect=volumes)

    assert result["isError"] is False
    sent = fake.posts[0]
    assert sent["path"] == "/api/v1/volumes/UUID-1/mount"
    assert sent["extra"] == {"X-NTFSMNT-GRANT": "g_1"}
    assert sent["body"] == {"label": "HD1", "recover": False,
                            "remove_hiberfile": False}


def test_unmount_and_eject_use_the_same_path():
    fake = FakeController(post=lambda p, b, e: {"ok": True})

    call_tool("unmount_volume", {"volume": "HD1", "grant_id": "g_1"},
              controller=fake, collect=volumes)
    call_tool("eject_volume", {"volume": "HD1", "grant_id": "g_2"},
              controller=fake, collect=volumes)

    assert [p["path"] for p in fake.posts] == [
        "/api/v1/volumes/UUID-1/unmount",
        "/api/v1/volumes/UUID-1/eject",
    ]
    assert [p["extra"]["X-NTFSMNT-GRANT"] for p in fake.posts] == ["g_1", "g_2"]


def test_controller_error_becomes_iserror_true_with_a_code():
    def fail(path, body, extra):
        raise ControllerError("grant_expired", "the grant expired", "ask again")

    fake = FakeController(post=fail)

    result = call_tool("mount_volume", {"volume": "HD1", "grant_id": "g_1"},
                       controller=fake, collect=volumes)

    assert result["isError"] is True
    error = body_of(result)["error"]
    assert error["code"] == "grant_expired"
    assert error["next_step"] == "ask again"


def test_read_tools_never_start_the_controller():
    def boom():
        raise AssertionError("controller must not start for read tools")

    stdin = io.StringIO(
        '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/call",'
        '"params":{"name":"list_volumes"}}\n')
    stdout = io.StringIO()

    serve(stdin, stdout, controller_factory=boom, collect=lambda: [])

    replies = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert len(replies) == 2


def test_action_tool_without_a_controller_is_a_structured_error():
    result = call_tool("mount_volume", {"volume": "HD1", "grant_id": "g_1"},
                       collect=volumes)

    assert result["isError"] is True
    assert body_of(result)["error"]["code"] == "controller_unavailable"


def test_serve_ignores_notifications_and_records_the_client_name():
    seen = {}

    class Session:
        controller = FakeController(
            post=lambda p, b, e: (seen.update(b) or
                                  {"request_id": "r_1", "status": "approved",
                                   "grant_id": "g_1"}))

        def close(self):
            seen["closed"] = True

    stdin = io.StringIO(
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"clientInfo":{"name":"Claude Desktop"}}}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":'
        '{"name":"request_grant","arguments":{"volume":"HD1",'
        '"actions":["mount"]}}}\n')
    stdout = io.StringIO()

    serve(stdin, stdout, controller_factory=Session, collect=volumes,
          sleep=lambda s: None, clock=Clock())

    lines = stdout.getvalue().splitlines()
    assert len(lines) == 2  # the notification got no reply
    assert seen["client"] == "Claude Desktop"
    assert seen["closed"] is True
