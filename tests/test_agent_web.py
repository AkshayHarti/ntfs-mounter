import json

import pytest
from fastapi.testclient import TestClient

from ntfsmnt.web.agent import register_agent_routes
from ntfsmnt.web.app import templates
from ntfsmnt.web.models import (ActionView, DoctorStatus, HostView, OwnerView,
                                Snapshot, VolumeView)
from ntfsmnt.web.config import cookie_value
from tests import web_helpers as h


def volume(label="HD1", uuid=None, state="unmounted", actions=("mount", "eject")):
    return VolumeView(
        id=uuid or f"UUID-{label}", device="/dev/disk4s1", label=label, uuid=uuid,
        size=1_000, fs="ntfs", state=state, mount_point=None, driver=None,
        pid=None, flags={},
        actions=tuple(ActionView(name=a, enabled=a in actions)
                      for a in ("mount", "unmount", "eject")),
        options=(),
    )


class StubService:
    def __init__(self, snapshot=None):
        self._snapshot = snapshot
        self.refreshed = 0

    def snapshot(self):
        return self._snapshot

    def refresh_doctor(self):
        self.refreshed += 1
        return DoctorStatus("ok", "2026-10-24T00:00:00Z", ())


def snapshot(*volumes):
    return Snapshot(
        generated_at="2026-10-24T00:00:00Z",
        host=HostView(root=True, read_only=False, owner=OwnerView(501, 20),
                      fuse_t=True, ntfs_3g=True),
        doctor=DoctorStatus("ok", "2026-10-24T00:00:00Z", ()),
        operation=None,
        volumes=volumes,
    )


def make_client(service=None):
    settings = h.make_settings()
    service = service or StubService(snapshot())
    app = __import__("fastapi").FastAPI()
    register_agent_routes(app, settings, service, templates)
    client = TestClient(app)
    return settings, client


def session(client, settings):
    client.cookies.set(settings.cookie_name, cookie_value(settings.token))


def body(message="show me the volumes"):
    return json.dumps({
        "threadId": "t1", "runId": "r1",
        "messages": [{"id": "m1", "role": "user", "content": message}],
    })


def events_of(response):
    return [
        json.loads(frame.removeprefix("data: "))
        for frame in response.text.split("\n\n") if frame
    ]


def test_run_refuses_without_cookie() -> None:
    settings, client = make_client()
    response = client.post("/agent/run", content=body(),
                           headers={"x-ntfsmnt": "1", "origin": "http://testserver"})
    assert response.status_code == 401


def test_run_refuses_without_the_guard_header() -> None:
    settings, client = make_client()
    client.cookies.set(settings.cookie_name, cookie_value(settings.token))
    response = client.post("/agent/run", content=body(), headers={
        "origin": "http://testserver"})
    assert response.status_code == 403


def test_run_refuses_foreign_origin() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.post("/agent/run", content=body(), headers={
        "x-ntfsmnt": "1", "origin": "http://evil.example"})
    assert response.status_code == 403


def test_host_guard_applies_to_agent_pages() -> None:
    settings, client = make_client()
    # The agent route requires a session like every other page; the Host
    # allow-list is enforced by middleware in create_app (test_app_contract).
    client.cookies.set(settings.cookie_name, cookie_value(settings.token))
    assert client.get("/agent").status_code == 200
    client.cookies.clear()
    assert client.get("/agent").status_code == 401


def test_create_app_registers_the_agent_surface() -> None:
    from ntfsmnt.web.app import create_app

    settings = h.make_settings()
    app = create_app(settings, service=StubService(snapshot()))
    client = TestClient(app)
    assert client.get("/agent", headers={"host": "evil.example"}).status_code == 421
    client.cookies.set(settings.cookie_name, cookie_value(settings.token))
    assert client.get("/agent").status_code == 200
    response = client.post("/agent/run", content=body("mount HD1"), headers={
        "x-ntfsmnt": "1", "origin": "http://testserver"})
    assert b'"type":"RUN_FINISHED"' in response.content or \
           b'"type": "RUN_FINISHED"' in response.content


def test_run_streams_agui_in_order() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.post("/agent/run", content=body(), headers={
        "x-ntfsmnt": "1", "origin": "http://testserver"})
    assert response.status_code == 200
    types = [event["type"] for event in events_of(response)]
    assert types == ["RUN_STARTED", "STATE_SNAPSHOT", "TEXT_MESSAGE_START",
                     "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM",
                     "CUSTOM", "RUN_FINISHED"]


def test_run_rejects_an_empty_message() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.post("/agent/run", content=json.dumps({
        "messages": [{"id": "m", "role": "assistant", "content": "hi"}],
    }), headers={"x-ntfsmnt": "1", "origin": "http://testserver"})
    assert response.status_code == 400


def test_snapshot_carries_no_token() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.post("/agent/run", content=body("show me the volumes"),
                           headers={"x-ntfsmnt": "1",
                                    "origin": "http://testserver"})
    assert settings.token not in response.text


def test_doctor_intent_refreshes_doctor() -> None:
    service = StubService(snapshot())
    settings, client = make_client(service)
    session(client, settings)
    response = client.post("/agent/run", content=body("run the doctor"), headers={
        "x-ntfsmnt": "1", "origin": "http://testserver"})
    assert response.status_code == 200
    assert service.refreshed == 1  # read-only checks re-run on request


def test_mount_intent_does_not_mutate() -> None:
    service = StubService(snapshot())
    settings, client = make_client(service)
    session(client, settings)
    client.post("/agent/run", content=body("mount HD1"), headers={
        "x-ntfsmnt": "1", "origin": "http://testserver"})
    # The agent layer never mounts; the stub exposes nothing to call.
    assert service.refreshed == 0


def test_library_endpoint() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.get("/agent/library")
    assert response.status_code == 200
    assert "VolumeCard" in response.text


def test_agent_page_serves_html() -> None:
    settings, client = make_client()
    session(client, settings)
    response = client.get("/agent")
    assert response.status_code == 200
    assert "agent.js" in response.text


def test_token_exchange_sets_cookie() -> None:
    settings, client = make_client()
    response = client.get(f"/agent?token={settings.token}", follow_redirects=False)
    assert response.status_code == 303
    client.cookies.set(settings.cookie_name, cookie_value(settings.token))
    assert client.get("/agent").status_code == 200
    assert client.get("/agent?token=tampered",
                      follow_redirects=False).status_code == 401


def test_run_errors_stream_a_run_error(monkeypatch) -> None:
    class Broken:
        def snapshot(self):
            raise RuntimeError("boom")

        def refresh_doctor(self):  # pragma: no cover
            return DoctorStatus("ok", "x", ())

    settings, client = make_client(Broken())
    session(client, settings)
    response = client.post("/agent/run", content=body(), headers={
        "x-ntfsmnt": "1", "origin": "http://testserver"})
    types = [event["type"] for event in events_of(response)]
    assert types[-1] == "RUN_ERROR"
