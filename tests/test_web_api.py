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
