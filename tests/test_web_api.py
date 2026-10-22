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


def test_htmx_form_post_returns_fragment(monkeypatch):
    from ntfsmnt.mounter import MountResult
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(
        svc_mod, "mount_volume",
        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 4242))
    c, svc = client()
    calls = []
    real_mount = svc.mount

    # Service.mount is where the decoded ActionRequest fields land;
    # mount_volume itself only receives the resolved volume.
    def spy_mount(target, *, label, size=None, **kw):
        calls.append({"label": label, "size": size})
        return real_mount(target, label=label, size=size, **kw)

    monkeypatch.setattr(svc, "mount", spy_mount)
    r = c.post("/api/v1/volumes/UUID-1/mount",
               data={"label": "HD1", "size": "1000000000"},
               headers={**auth(c), "HX-Request": "true"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="detail"' in r.text
    assert "HD1" in r.text
    assert calls[0]["label"] == "HD1"
    assert calls[0]["size"] == 1000000000


def test_page_renders_master_detail():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert 'id="volumes-region"' in r.text
    assert "HD1" in r.text


def test_partial_requires_session():
    c, _ = client()
    assert c.get("/partials/volumes").status_code == 401


def test_partial_returns_region():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/partials/volumes?selected=UUID-1")
    assert r.status_code == 200
    assert 'id="volumes-region"' in r.text
    assert 'id="detail"' in r.text


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


def test_partial_empty_selection_means_no_selection():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/partials/volumes?selected=")
    assert r.status_code == 200
    assert 'data-selected="false"' in r.text
    assert "Select a drive" in r.text


def test_partial_without_selection_picks_first_volume():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/partials/volumes")
    assert r.status_code == 200
    assert 'data-selected="true"' in r.text
    assert "UUID-1" in r.text
