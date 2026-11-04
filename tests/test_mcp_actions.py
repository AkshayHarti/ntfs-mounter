import io
import json

import pytest

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


from types import SimpleNamespace
from ntfsmnt.mcp_client import open_approval


def test_browser_mode_calls_open_url_and_returns_true():
    seen = []
    assert open_approval("http://127.0.0.1:1/approve/r_1?nonce=n",
                         open_url=lambda url: seen.append(url) or True) is True
    assert seen == ["http://127.0.0.1:1/approve/r_1?nonce=n"]


def test_touchid_pipes_the_nonce_on_stdin_not_argv(tmp_path):
    helper = tmp_path / "ntfsmnt-approve"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs["input"]
        return SimpleNamespace(returncode=0)

    result = open_approval(
        "http://127.0.0.1:1/approve/r_1?nonce=SECRET", mode="touchid",
        helper=str(helper),
        request={"request_id": "r_1", "nonce": "SECRET", "volume": "HD1",
                 "actions": ["mount"]},
        run=run, open_url=lambda url: pytest.fail("must not open a browser"),
    )

    assert result is True
    assert "SECRET" not in " ".join(seen["argv"])
    assert "SECRET" in seen["input"]


def test_missing_helper_falls_back_to_the_browser_once(tmp_path):
    seen = []
    result = open_approval(
        "http://x/approve", mode="touchid",
        helper=str(tmp_path / "nope"), request={"nonce": "n"},
        open_url=lambda url: seen.append(url) or True,
    )

    assert result is True
    assert seen == ["http://x/approve"]


def test_helper_exit_2_falls_back_to_the_browser(tmp_path):
    helper = tmp_path / "ntfsmnt-approve"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)
    seen = []

    result = open_approval(
        "http://x/approve", mode="touchid", helper=str(helper),
        request={"nonce": "n"},
        run=lambda argv, **kw: SimpleNamespace(returncode=2),
        open_url=lambda url: seen.append(url) or True,
    )

    assert result is True
    assert seen == ["http://x/approve"]


def test_helper_exit_0_does_not_open_the_browser(tmp_path):
    helper = tmp_path / "ntfsmnt-approve"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)

    result = open_approval(
        "http://x/approve", mode="touchid", helper=str(helper),
        request={"nonce": "n"},
        run=lambda argv, **kw: SimpleNamespace(returncode=0),
        open_url=lambda url: pytest.fail("must not open a browser"),
    )

    assert result is True


def test_helper_exit_3_is_a_denial_not_a_fallback(tmp_path):
    helper = tmp_path / "ntfsmnt-approve"
    helper.write_text("#!/bin/sh\n")
    helper.chmod(0o755)

    result = open_approval(
        "http://x/approve", mode="touchid", helper=str(helper),
        request={"nonce": "n"},
        run=lambda argv, **kw: SimpleNamespace(returncode=3),
        open_url=lambda url: pytest.fail("must not open a browser"),
    )

    assert result is False


def test_no_browser_mode_prints_the_url_to_stderr(capsys):
    result = open_approval("http://x/approve", mode="none",
                           open_url=lambda url: pytest.fail("must not open"))

    captured = capsys.readouterr()
    assert result is False
    assert "http://x/approve" in captured.err
    assert captured.out == ""


def test_request_grant_opens_the_page_and_never_returns_the_url():
    opened = []
    fake = FakeController(
        post=lambda p, b, e: {"request_id": "r_1", "status": "approved",
                              "grant_id": "g_1", "wait_seconds": 0,
                              "approval_url":
                                  "http://127.0.0.1:9/approve/r_1?nonce=n"})

    result = call_tool("request_grant", {"volume": "HD1", "actions": ["mount"]},
                       controller=fake, sleep=lambda s: None, clock=Clock(),
                       opener=lambda url, **kw: opened.append((url, kw)))

    assert opened[0][0] == "http://127.0.0.1:9/approve/r_1?nonce=n"
    assert opened[0][1]["request"]["nonce"] == "n"
    assert "approval_url" not in result["content"][0]["text"]


def test_mcp_parser_accepts_approve_and_no_browser():
    from ntfsmnt.cli import _build_parser

    args = _build_parser().parse_args(["mcp", "--approve", "touchid",
                                       "--no-browser"])
    assert args.approve == "touchid"
    assert args.no_browser is True
