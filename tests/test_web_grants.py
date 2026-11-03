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


def pending_of(service, c, **body):
    request_id = request_grant(c, **body).json()["request_id"]
    return request_id, service.grants.pending(request_id)


def test_approval_page_lists_volume_actions_ttl_and_reason():
    c, service = client()
    request_id, pending = pending_of(service, c, reason="copy footage")

    r = c.get(f"/approve/{request_id}?nonce={pending.nonce}")

    assert r.status_code == 200
    for text in ("HD1", "mount", "unmount", "900", "copy footage", "Claude Desktop"):
        assert text in r.text


def test_approval_page_rejects_a_wrong_nonce_without_details():
    c, service = client()
    request_id, _ = pending_of(service, c)

    r = c.get(f"/approve/{request_id}?nonce=wrong")

    assert r.status_code == 403
    assert "HD1" not in r.text


def test_approval_page_sets_referrer_policy_no_referrer():
    c, service = client()
    request_id, pending = pending_of(service, c)

    r = c.get(f"/approve/{request_id}?nonce={pending.nonce}")

    assert r.headers["referrer-policy"] == "no-referrer"


def test_approve_needs_origin_and_nonce():
    c, service = client()
    request_id, pending = pending_of(service, c)

    no_origin = c.post(f"/api/v1/grants/{request_id}/approve",
                       headers={"X-NTFSMNT-APPROVAL": pending.nonce})
    assert no_origin.status_code == 403

    no_nonce = c.post(f"/api/v1/grants/{request_id}/approve",
                      headers={"Origin": "http://testserver"})
    assert no_nonce.status_code == 403


def test_approve_with_nonce_and_origin_mints_the_grant():
    c, service = client()
    request_id, pending = pending_of(service, c)

    r = c.post(f"/api/v1/grants/{request_id}/approve",
               headers={"Origin": "http://testserver",
                        "X-NTFSMNT-APPROVAL": pending.nonce})

    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert service.grants.pending(request_id).status == "approved"


def test_approve_rejects_an_agent_token():
    c, service = client()
    request_id, pending = pending_of(service, c)

    r = c.post(f"/api/v1/grants/{request_id}/approve",
               headers={"Origin": "http://testserver",
                        "X-NTFSMNT-APPROVAL": pending.nonce,
                        "Authorization": "Bearer agent"})

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "human_approval_required"


def test_nonce_is_single_use():
    c, service = client()
    request_id, pending = pending_of(service, c)
    headers = {"Origin": "http://testserver", "X-NTFSMNT-APPROVAL": pending.nonce}

    assert c.post(f"/api/v1/grants/{request_id}/approve", headers=headers).status_code == 200
    second = c.post(f"/api/v1/grants/{request_id}/approve", headers=headers)

    assert second.status_code == 403
    assert second.json()["error"]["code"] == "approval_consumed"


def test_deny_marks_the_request_denied():
    c, service = client()
    request_id, pending = pending_of(service, c)

    r = c.post(f"/api/v1/grants/{request_id}/deny",
               headers={"Origin": "http://testserver",
                        "X-NTFSMNT-APPROVAL": pending.nonce})

    assert r.status_code == 200
    assert r.json()["status"] == "denied"
    assert service.grants.pending(request_id).status == "denied"


def test_approve_and_deny_are_audited():
    from ntfsmnt.control.audit import AuditLog
    from ntfsmnt.web.service import Service as Svc

    settings = make_settings(agent_token="agent")
    service = Svc(settings, discover=lambda: [make_volume()], run=FakeRun(),
                  mounts=lambda: [], audit=AuditLog(_tmp_audit_path()),
                  doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                                 lambda p: "libfuse-t", lambda: None))
    c = TestClient(create_app(settings, service=service))
    request_id, pending = pending_of(service, c)

    c.post(f"/api/v1/grants/{request_id}/approve",
           headers={"Origin": "http://testserver",
                    "X-NTFSMNT-APPROVAL": pending.nonce})

    events = [entry["event"] for entry in service.audit.read()]
    assert "grant.approved" in events


def _tmp_audit_path():
    import tempfile
    from pathlib import Path
    return Path(tempfile.mkdtemp()) / "audit.jsonl"
