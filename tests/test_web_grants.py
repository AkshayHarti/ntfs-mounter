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


from ntfsmnt.mounttable import MountEntry
import ntfsmnt.web.service as svc_mod
from ntfsmnt.control.lock import OperationFileLock
from ntfsmnt.control.audit import AuditLog, AuditUnavailable
from ntfsmnt.web.service import Service as Svc

RW = MountEntry(source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs",
                read_only=False)


def agent_action(c, *, action="mount", volume="UUID-1", grant=None, **body):
    payload = {"label": "HD1"}
    payload.update(body)
    headers = {"Authorization": "Bearer agent", "X-NTFSMNT": "1",
               "Origin": "http://testserver"}
    if grant is not None:
        headers["X-NTFSMNT-GRANT"] = grant
    return c.post(f"/api/v1/volumes/{volume}/{action}", json=payload, headers=headers)


def approved_grant(c, service, **body):
    request_id = request_grant(c, **body).json()["request_id"]
    return service.grants.approve(request_id)


def fake_mount(monkeypatch, *, fail=None):
    calls = {}

    def mount(volume, **kw):
        calls.update(kw, volume=volume)
        if fail:
            raise fail
        from ntfsmnt.mounter import MountResult
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=4242)

    monkeypatch.setattr(svc_mod, "mount_volume", mount)
    return calls


def test_agent_mount_without_grant_is_403_grant_required():
    c, _ = client()

    r = agent_action(c)

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "grant_required"


def test_agent_mount_with_a_grant_runs_the_same_service_call(monkeypatch):
    c, service = client()
    calls = fake_mount(monkeypatch)
    grant = approved_grant(c, service)

    r = agent_action(c, grant=grant.id)

    assert r.status_code == 200
    assert r.json()["result"]["ok"] is True
    assert calls["volume"].device.uuid == "UUID-1"
    assert calls["run"] is service._run
    assert calls["uid"] == 501 and calls["gid"] == 20


def test_agent_remove_hiberfile_is_refused_before_any_runner_call(monkeypatch):
    c, service = client()
    calls = fake_mount(monkeypatch)
    grant = approved_grant(c, service)

    r = agent_action(c, grant=grant.id, remove_hiberfile=True, confirm="HD1")

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "manual_required"
    assert calls == {}


def test_agent_recover_is_refused_before_any_runner_call(monkeypatch):
    c, service = client()
    calls = fake_mount(monkeypatch)
    grant = approved_grant(c, service)

    r = agent_action(c, grant=grant.id, recover=True)

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "manual_required"
    assert calls == {}


def test_grant_for_one_volume_cannot_mount_another(monkeypatch):
    c, service = client()
    calls = fake_mount(monkeypatch)
    grant = approved_grant(c, service)

    r = agent_action(c, grant=grant.id, volume="UUID-2")

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "grant_mismatch"
    assert calls == {}


def test_mount_needs_the_grant_header_not_just_the_agent_token(monkeypatch):
    c, _ = client()
    fake_mount(monkeypatch)

    r = agent_action(c, grant="g_made_up")

    assert r.status_code == 403
    assert r.json()["error"]["code"] == "grant_required"


def test_operation_in_progress_when_the_file_lock_is_held(tmp_path, monkeypatch):
    fake_mount(monkeypatch)
    path = tmp_path / "ntfsmnt.lock"
    held = OperationFileLock(path)
    assert held.acquire() is True
    try:
        svc = Svc(make_settings(agent_token="agent"), discover=lambda: [make_volume()],
                  run=FakeRun(), mounts=lambda: [],
                  file_lock=OperationFileLock(path),
                  doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                                 lambda p: "libfuse-t", lambda: None))
        result = svc.mount("UUID-1", label="HD1")
    finally:
        held.release()

    assert result.ok is False
    assert result.error.code == "operation_in_progress"


def _service(tmp_path, *, audit=None, lock=None, volumes=None):
    return Svc(
        make_settings(agent_token="agent"),
        discover=lambda: list(volumes or [make_volume()]),
        run=FakeRun(), mounts=lambda: [],
        audit=audit, file_lock=lock,
        doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                       lambda p: "libfuse-t", lambda: None),
    )


def test_successful_mount_appends_attempt_and_ok(tmp_path, monkeypatch):
    fake_mount(monkeypatch)
    audit = AuditLog(tmp_path / "audit.jsonl")
    svc = _service(tmp_path, audit=audit)

    result = svc.mount("UUID-1", label="HD1")

    assert result.ok is True
    events = [entry["event"] for entry in audit.read()]
    assert events == ["mount.attempt", "mount.ok"]


def test_failed_mount_appends_attempt_and_fail(tmp_path, monkeypatch):
    from ntfsmnt.mounter import MountFailed
    fake_mount(monkeypatch, fail=MountFailed("nope"))
    audit = AuditLog(tmp_path / "audit.jsonl")
    svc = _service(tmp_path, audit=audit)

    result = svc.mount("UUID-1", label="HD1")

    assert result.ok is False
    assert [entry["event"] for entry in audit.read()] == [
        "mount.attempt", "mount.fail"]


def test_audit_failure_blocks_mount_before_any_runner_call(tmp_path, monkeypatch):
    calls = fake_mount(monkeypatch)
    audit = AuditLog(tmp_path / "missing" / "audit.jsonl")
    svc = _service(tmp_path, audit=audit)

    result = svc.mount("UUID-1", label="HD1")

    assert result.ok is False
    assert result.error.code == "audit_unavailable"
    assert result.error.status == 503
    assert calls == {}


class _OutcomeFails:
    def __init__(self):
        self.events = []

    def append(self, event, **fields):
        self.events.append(event)
        if event.endswith(".ok"):
            raise AuditUnavailable("disk full")

    def read(self, limit=200):
        return []


def test_audit_failure_after_success_still_reports_success(tmp_path, monkeypatch):
    fake_mount(monkeypatch)
    audit = _OutcomeFails()
    svc = _service(tmp_path, audit=audit)

    result = svc.mount("UUID-1", label="HD1")

    assert result.ok is True
    assert audit.events == ["mount.attempt", "mount.ok"]


def test_audit_failure_blocks_eject_before_any_runner_call(tmp_path, monkeypatch):
    fake_mount(monkeypatch)
    audit = AuditLog(tmp_path / "missing" / "audit.jsonl")
    svc = _service(tmp_path, audit=audit)

    result = svc.eject("UUID-1", label="HD1")

    assert result.ok is False
    assert result.error.code == "audit_unavailable"
    assert result.error.status == 503


def test_audit_failure_does_not_block_unmount(tmp_path, monkeypatch):
    def fake_unmount(volume, **kw):
        return True

    monkeypatch.setattr(svc_mod, "unmount_volume", fake_unmount)
    audit = AuditLog(tmp_path / "missing" / "audit.jsonl")
    volume = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    svc = _service(tmp_path, audit=audit, volumes=[volume])

    result = svc.unmount("UUID-1", label="HD1")

    assert result.ok is True
