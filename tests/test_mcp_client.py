import io
import json
import subprocess
import urllib.error

import pytest

from ntfsmnt.mcp_client import (
    Controller,
    ControllerError,
    ControllerSession,
    start_controller,
)


class FakeResponse:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body


def fake_http(*, result=None, error=None, calls=None):
    def http(request, timeout=None):
        if calls is not None:
            calls.append((request, timeout))
        if error is not None:
            raise error
        return FakeResponse(json.dumps(result or {"ok": True}).encode())
    return http


def test_controller_sets_bearer_header_origin_and_mutation_header():
    calls = []
    c = Controller("http://127.0.0.1:9123", "agent",
                   http=fake_http(result={"ok": True}, calls=calls))

    assert c.post("/api/v1/grants", {"volume": "HD1"}) == {"ok": True}

    request, timeout = calls[0]
    assert request.full_url == "http://127.0.0.1:9123/api/v1/grants"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer agent"
    assert request.get_header("Origin") == "http://127.0.0.1:9123"
    assert request.get_header("X-ntfsmnt") == "1"
    assert json.loads(request.data) == {"volume": "HD1"}


def test_controller_get_and_delete_work():
    calls = []
    c = Controller("http://127.0.0.1:9123", "agent",
                   http=fake_http(result={"ok": True}, calls=calls))

    c.get("/api/v1/state")
    c.delete("/api/v1/grants/g_1")

    assert [request.get_method() for request, _ in calls] == ["GET", "DELETE"]


def test_http_error_body_maps_to_controller_error():
    body = io.BytesIO(json.dumps({
        "error": {"code": "grant_required", "message": "need a grant",
                  "next_step": "call request_grant"}
    }).encode())
    error = urllib.error.HTTPError(
        "http://127.0.0.1:9123/x", 403, "Forbidden", {}, body)
    c = Controller("http://127.0.0.1:9123", "agent", http=fake_http(error=error))

    with pytest.raises(ControllerError) as exc:
        c.post("/x")

    assert exc.value.code == "grant_required"
    assert exc.value.next_step == "call request_grant"


def test_unreachable_controller_is_a_structured_error():
    error = urllib.error.URLError("connection refused")
    c = Controller("http://127.0.0.1:9123", "agent", http=fake_http(error=error))

    with pytest.raises(ControllerError) as exc:
        c.get("/api/v1/state")

    assert exc.value.code == "controller_unavailable"


class FakeChild:
    def __init__(self, line=None, *, exit_on_wait=False):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(line or "")
        self.stderr = io.StringIO("")
        self.argv = None
        self.terminated = False
        self.killed = False
        self.waits = []
        self.stdin_was_closed_on_wait = None
        self._exit_on_wait = exit_on_wait

    def wait(self, timeout=None):
        self.waits.append(timeout)
        self.stdin_was_closed_on_wait = self.stdin.closed
        if self._exit_on_wait or self.terminated:
            return 0
        raise subprocess.TimeoutExpired("child", timeout)

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def fake_popen(child, seen=None):
    def popen(argv, **kwargs):
        child.argv = argv
        if seen is not None:
            seen.update(kwargs)
        return child
    return popen


def test_handshake_parses_the_json_line():
    child = FakeChild('{"ntfsmnt":"mcp-session","port":9123,"agent_token":"agent"}\n')

    session = start_controller(uid=501, gid=20, popen=fake_popen(child))

    assert session.process is child
    assert session.controller.base_url == "http://127.0.0.1:9123"
    assert session.controller.agent_token == "agent"
    assert child.argv == [
        "/usr/bin/sudo", "-n", "/usr/local/libexec/ntfsmnt/bin/ntfsmnt",
        "web", "--mcp-session", "--port", "0", "--owner", "501:20",
        "--exit-on-stdin-eof",
    ]


def test_handshake_timeout_terminates_only_its_own_child():
    import threading

    release = threading.Event()

    class BlockingStream:
        def readline(self):
            release.wait(5)
            return ""

    child = FakeChild("")
    child.stdout = BlockingStream()

    try:
        with pytest.raises(ControllerError) as exc:
            start_controller(uid=501, gid=20, popen=fake_popen(child), timeout=0.05)
    finally:
        release.set()

    assert exc.value.code == "controller_unavailable"
    assert child.terminated is True
    assert child.killed is False


def test_close_closes_stdin_first_so_the_controller_exits_on_eof():
    child = FakeChild('{"ntfsmnt":"mcp-session","port":9123,"agent_token":"a"}\n')
    session = start_controller(uid=501, gid=20, popen=fake_popen(child))

    session.close(grace=0)

    assert child.stdin.closed is True
    assert child.stdin_was_closed_on_wait is True
    assert child.terminated is True
    assert child.killed is False


def test_close_leaves_a_child_that_exited_alone():
    child = FakeChild('{"ntfsmnt":"mcp-session","port":9123,"agent_token":"a"}\n',
                     exit_on_wait=True)
    session = start_controller(uid=501, gid=20, popen=fake_popen(child))

    session.close()

    assert child.terminated is False
    assert child.killed is False
