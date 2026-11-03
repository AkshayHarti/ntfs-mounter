import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ntfsmnt.control.grants import GrantStore
from ntfsmnt.doctor import Env
from ntfsmnt.web.app import create_app
from ntfsmnt.web.service import Service
from tests.web_helpers import FakeRun, make_settings, make_volume

AGENT = {"Authorization": "Bearer agent"}


def client(*, read_only=False):
    settings = make_settings(read_only=read_only, agent_token="agent")
    service = Service(
        settings,
        discover=lambda: [make_volume(), make_volume(uuid="UUID-2", label="HD2")],
        run=FakeRun(),
        mounts=lambda: [],
        grants=GrantStore(),
        doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                       lambda p: "libfuse-t", lambda: None),
    )
    return TestClient(create_app(settings, service=service)), service


def request_grant(c, **body):
    payload = {"volume": "HD1", "actions": ["mount"], "reason": "copy footage",
               "client": "Claude Desktop"}
    payload.update(body)
    return c.post("/api/v1/grants", json=payload, headers=AGENT)


def test_request_grant_returns_a_pending_request_and_a_nonce_url():
    c, service = client()

    r = request_grant(c)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "pending"
    assert body["wait_seconds"] > 0
    pending = service.grants.pending(body["request_id"])
    assert pending is not None
    assert body["approval_url"].endswith(
        f"/approve/{pending.id}?nonce={pending.nonce}")


def test_request_grant_rejects_recover_with_manual_required():
    c, _ = client()

    r = request_grant(c, actions=["recover"])

    assert r.status_code == 400
    error = r.json()["error"]
    assert error["code"] == "manual_required"
    assert "ntfsmnt mount --recover" in error["next_step"]


def test_request_grant_rejects_remove_hiberfile_with_manual_required():
    c, _ = client()

    r = request_grant(c, actions=["remove_hiberfile"])

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "manual_required"
    assert "ntfsmnt mount --remove-hiberfile" in r.json()["error"]["next_step"]


def test_request_grant_rejects_an_unknown_action():
    c, _ = client()

    r = request_grant(c, actions=["mount", "repair"])

    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_request"


def test_request_grant_rejects_unknown_volume():
    c, _ = client()

    r = request_grant(c, volume="NOPE")

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "volume_not_found"


def test_poll_reports_pending_then_approved():
    c, service = client()
    request_id = request_grant(c).json()["request_id"]

    pending = c.get(f"/api/v1/grants/{request_id}", headers=AGENT)
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"

    grant = service.grants.approve(request_id)

    approved = c.get(f"/api/v1/grants/{request_id}", headers=AGENT)
    assert approved.status_code == 200
    assert approved.json() == {
        "request_id": request_id, "status": "approved", "grant_id": grant.id}


def test_poll_after_deny_reports_denied():
    c, service = client()
    request_id = request_grant(c).json()["request_id"]

    service.grants.deny(request_id)

    r = c.get(f"/api/v1/grants/{request_id}", headers=AGENT)
    assert r.json()["status"] == "denied"


def test_poll_unknown_request_is_404():
    c, _ = client()

    r = c.get("/api/v1/grants/r_nope", headers=AGENT)

    assert r.status_code == 404
    assert r.json()["error"]["code"] == "grant_required"


def test_list_and_revoke():
    c, service = client()
    request_id = request_grant(c).json()["request_id"]
    grant = service.grants.approve(request_id)

    listed = c.get("/api/v1/grants", headers=AGENT)
    assert listed.status_code == 200
    entry = listed.json()["grants"][0]
    assert entry["grant_id"] == grant.id
    assert entry["volume"] == "HD1"
    assert entry["device"] == "/dev/disk4s1"
    assert entry["actions"] == ["mount", "unmount"]

    revoked = c.delete(f"/api/v1/grants/{grant.id}", headers=AGENT)
    assert revoked.status_code == 200
    assert revoked.json() == {"revoked": True}

    again = c.delete(f"/api/v1/grants/{grant.id}", headers=AGENT)
    assert again.json() == {"revoked": False}


def test_routes_require_the_agent_token():
    c, _ = client()

    assert c.get("/api/v1/grants").status_code == 401
    assert c.post("/api/v1/grants", json={"volume": "HD1",
                                          "actions": ["mount"]}).status_code == 401
    assert c.get("/api/v1/grants/r_x").status_code == 401
    assert c.delete("/api/v1/grants/g_x").status_code == 401
