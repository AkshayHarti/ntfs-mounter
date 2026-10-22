import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ntfsmnt.doctor import Env
from ntfsmnt.web.app import create_app
from ntfsmnt.web.service import Service
from tests.web_helpers import FakeRun, make_settings, make_volume


def client(*, read_only=False):
    settings = make_settings(read_only=read_only)
    service = Service(
        settings,
        discover=lambda: [make_volume()],
        run=FakeRun(),
        mounts=lambda: [],
        doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                       lambda p: "libfuse-t", lambda: None),
    )
    return TestClient(create_app(settings, service=service)), service


def test_missing_cookie_is_401():
    c, _ = client()
    r = c.get("/api/v1/state")
    assert r.status_code == 401


def test_token_exchange_sets_cookie_and_redirects():
    c, _ = client()
    r = c.get("/?token=tok", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "ntfsmnt_session" in r.cookies


def test_wrong_token_is_401():
    c, _ = client()
    assert c.get("/?token=nope", follow_redirects=False).status_code == 401


def test_foreign_host_is_421():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/api/v1/state", headers={"host": "evil.test"})
    assert r.status_code == 421


def test_state_shape_and_read_only_host():
    c, _ = client(read_only=True)
    c.get("/?token=tok")
    data = c.get("/api/v1/state").json()
    assert data["host"]["read_only"] is True
    assert data["volumes"][0]["id"] == "UUID-1"
    assert data["doctor"]["status"] == "ok"


def test_activity_is_empty_initially():
    c, _ = client()
    c.get("/?token=tok")
    assert c.get("/api/v1/activity").json() == {"entries": []}


def auth(c):
    c.get("/?token=tok")
    return {"X-NTFSMNT": "1", "Origin": "http://testserver"}


def test_mutation_requires_header():
    c, _ = client()
    c.get("/?token=tok")
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 403


def test_mutation_requires_origin():
    c, _ = client()
    c.get("/?token=tok")
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1"}, headers={"X-NTFSMNT": "1"})
    assert r.status_code == 403


def test_unknown_action_is_404():
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/explode", json={"label": "HD1"}, headers=auth(c))
    assert r.status_code == 404


def test_guarded_mount_without_label_is_400(monkeypatch):
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1", "remove_hiberfile": True}, headers=auth(c))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "confirmation_mismatch"


def test_mount_success_returns_result_and_volume(monkeypatch):
    from ntfsmnt.mounter import MountResult
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(
        svc_mod, "mount_volume",
        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 4242))
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount", json={"label": "HD1"}, headers=auth(c))
    data = r.json()
    assert r.status_code == 200
    assert data["result"]["ok"] is True
    assert data["volume"]["label"] == "HD1"


def test_second_action_is_409_while_lock_held():
    c, svc = client()
    headers = auth(c)
    with svc.operation("mount", "HD1"):
        r = c.post("/api/v1/volumes/UUID-1/mount",
                   json={"label": "HD1"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "operation_in_progress"


def test_wrong_label_is_volume_changed():
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount", json={"label": "HD2"}, headers=auth(c))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "volume_changed"


def test_mount_success_publishes_state_event(monkeypatch):
    from ntfsmnt.mounter import MountResult
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(
        svc_mod, "mount_volume",
        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 4242))
    c, svc = client()
    r = c.post("/api/v1/volumes/UUID-1/mount", json={"label": "HD1"}, headers=auth(c))
    assert r.status_code == 200
    assert any("event: state" in f for f in svc.events.replay(0))
