import pytest

from ntfsmnt.doctor import Check, Env
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.web import service as service_module
from ntfsmnt.web.models import DoctorStatus, Finding
from ntfsmnt.web.service import Service, volume_id
from tests.web_helpers import FakeRun, make_settings, make_volume

RW = MountEntry(source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False)
RO = MountEntry(source="/dev/disk4s1", mount_point="/Volumes/HD1", fs="ntfs", read_only=True)


def make_service(volumes, *, doctor=None, run=None, read_only=False):
    settings = make_settings(read_only=read_only)
    svc = Service(
        settings,
        discover=lambda: list(volumes),
        run=run or FakeRun(),
        mounts=lambda: [],
        doctor_env=Env(
            path_exists=lambda p: True, run=lambda argv: (0, ""), euid=0,
            linkage=lambda p: "libfuse-t", first_device=lambda: None,
        ),
    )
    if doctor is not None:
        svc._doctor = doctor
    return svc


def test_volume_id_prefers_uuid():
    assert volume_id(make_volume(uuid="UUID-9")) == "UUID-9"


def test_volume_id_falls_back_to_identifier():
    assert volume_id(make_volume(uuid="")) == "dev-disk4s1"


def test_read_only_volume_offers_mount_and_eject():
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    view = svc.snapshot().volumes[0]
    assert view.state == "ro"
    assert {a.name: a.enabled for a in view.actions} == {
        "mount": True, "unmount": False, "eject": True,
    }


def test_rw_volume_offers_unmount_and_reports_pid():
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    svc = make_service([rw], run=FakeRun(pid="4242"))
    view = svc.snapshot().volumes[0]
    assert view.state == "rw"
    assert view.driver == "fuse-t"
    assert view.pid == 4242
    assert {a.name: a.enabled for a in view.actions}["unmount"] is True


def test_unmounted_volume_offers_mount_and_eject():
    svc = make_service([make_volume()])
    view = svc.snapshot().volumes[0]
    assert view.state == "unmounted"
    assert {a.name: a.enabled for a in view.actions} == {
        "mount": True, "unmount": False, "eject": True,
    }


def test_remove_hiberfile_is_guarded():
    svc = make_service([make_volume()])
    option = next(o for o in svc.snapshot().volumes[0].options
                  if o.name == "remove_hiberfile")
    assert option.guarded is True
    assert "hibernation" in (option.reason or "").lower()


def test_read_only_mode_disables_every_action():
    svc = make_service([make_volume()], read_only=True)
    view = svc.snapshot().volumes[0]
    assert view.actions and all(not a.enabled for a in view.actions)
    assert all("sudo ntfsmnt web" in (a.reason or "") for a in view.actions)


def test_empty_label_disables_mount():
    svc = make_service([make_volume(label="")])
    mount = next(a for a in svc.snapshot().volumes[0].actions if a.name == "mount")
    assert mount.enabled is False
    assert "label" in (mount.reason or "").lower()


def test_busy_device_access_warns_without_disabling_actions(monkeypatch):
    monkeypatch.setattr(service_module, "run_checks", lambda env: [
        Check("device-access", False,
              "ntfs-3g.probe failed on /dev/disk4s1 with exit code 16: "
              "Error opening '/dev/disk4s1': Resource busy",
              "The volume is mounted; unmount it first, then retry",
              advisory=True),
    ])
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    doc = svc.refresh_doctor()
    assert doc.status == "warning"
    assert [(f.check, f.severity) for f in doc.findings] == [("device-access", "warning")]
    view = svc.snapshot().volumes[0]
    assert {a.name: a.enabled for a in view.actions}["mount"] is True


def test_permission_denied_device_access_still_blocks(monkeypatch):
    monkeypatch.setattr(service_module, "run_checks", lambda env: [
        Check("device-access", False,
              "ntfs-3g.probe failed on /dev/disk4s1 with exit code 19: "
              "Error opening read-only '/dev/disk4s1': Permission denied",
              "Run as root via sudo from a terminal that has Full Disk Access",
              advisory=False),
    ])
    svc = make_service([make_volume()])
    doc = svc.refresh_doctor()
    assert doc.status == "blocker"
    assert [f.severity for f in doc.findings] == ["blocker"]


def test_doctor_blocker_disables_actions():
    doc = DoctorStatus(
        "blocker", "now",
        (Finding("blocker", "fuse-t", "libfuse-t missing", "install it"),),
    )
    svc = make_service([make_volume()], doctor=doc)
    view = svc.snapshot().volumes[0]
    assert all(not a.enabled for a in view.actions)
    assert any("fuse-t" in (a.reason or "") for a in view.actions)


def test_doctor_findings_map_to_severity(monkeypatch):
    def fake_checks(env):
        return [
            Check("macos", True, "macOS 26"),
            Check("fuse-t", False, "missing", "brew install --cask fuse-t"),
            Check("root", True, "root"),
        ]

    monkeypatch.setattr(service_module, "run_checks", fake_checks)
    svc = make_service([make_volume()])
    doc = svc.refresh_doctor()
    assert doc.status == "blocker"
    assert [(f.check, f.severity) for f in doc.findings] == [("fuse-t", "blocker")]
    assert svc.snapshot().host.fuse_t is False


from ntfsmnt.web.service import OperationInProgress
import ntfsmnt.web.service as svc_mod


def test_mount_success_uses_owner_and_flags(monkeypatch):
    calls = {}

    def fake_mount(volume, **kw):
        calls.update(kw)
        from ntfsmnt.mounter import MountResult
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=4242)

    monkeypatch.setattr(svc_mod, "mount_volume", fake_mount)
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD1", recover=True)
    assert result.ok is True
    assert calls["uid"] == 501 and calls["gid"] == 20
    assert calls["recover"] is True and calls["remove_hiberfile"] is False
    assert calls["label_shared"] is False


def test_mount_rejects_wrong_label_without_calling_mounter(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD2")
    assert result.error.code == "volume_changed"
    assert result.error.status == 409


def test_mount_rejects_wrong_size_without_calling_mounter(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume(size=1_000_000_000)])
    result = svc.mount("UUID-1", label="HD1", size=2_000_000_000)
    assert result.error.code == "volume_changed"


def test_mount_rejects_unknown_identity(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    result = svc.mount("UUID-404", label="HD1")
    assert result.error.code == "volume_not_found"
    assert result.error.status == 404


def test_duplicate_uuid_is_ambiguous(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume(), make_volume(identifier="disk5s1")])
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "ambiguous_volume"
    assert result.error.status == 409


def test_action_in_read_only_mode_is_forbidden(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()], read_only=True)
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code in {"not_root", "owner_unresolved"}
    assert result.error.status == 403


def test_action_with_doctor_blocker_is_409(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    doc = DoctorStatus(
        "blocker", "now",
        (Finding("blocker", "ntfs-3g", "ntfs-3g missing", "install it"),),
    )
    svc = make_service([make_volume()], doctor=doc)
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "doctor_blocker"
    assert result.error.status == 409


def test_guarded_mount_requires_exact_label(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    for confirm in (None, "HD", "hd1"):
        result = svc.mount("UUID-1", label="HD1", remove_hiberfile=True, confirm=confirm)
        assert result.error.code == "confirmation_mismatch"
        assert result.error.status == 400


def test_confirmed_remove_hiberfile_forwards_flag(monkeypatch):
    calls = {}

    def fake_mount(volume, **kw):
        calls.update(kw)
        from ntfsmnt.mounter import MountResult
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=4242)

    monkeypatch.setattr(svc_mod, "mount_volume", fake_mount)
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD1", remove_hiberfile=True, confirm="HD1")
    assert result.ok is True
    assert calls["remove_hiberfile"] is True
    assert calls["recover"] is False
    assert calls["uid"] == 501 and calls["gid"] == 20


def test_hibernated_failure_offers_guarded_mount(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed(
            "The volume is hibernated. Re-run with --remove-hiberfile. "
            "Apple's read-only mount was restored."
        )

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.restored is True
    assert result.error.code == "blocked_hibernated"
    assert result.error.status == 422
    assert result.error.next_step


def test_restore_attempt_is_not_reported_as_restored(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("The device vanished. Read-only restore attempted.")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.restored is False


def test_second_action_while_locked_is_refused(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    with svc.operation("mount", "HD1"):
        result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "operation_in_progress"
    assert result.error.status == 409


def test_mount_activity_is_recorded(monkeypatch):
    from ntfsmnt.mounter import MountResult
    monkeypatch.setattr(svc_mod, "mount_volume",
                        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 1))
    svc = make_service([make_volume()])
    svc.mount("UUID-1", label="HD1")
    entry = svc.activity()[0]
    assert (entry.action, entry.volume, entry.ok) == ("mount", "HD1", True)


def test_unmount_of_unmounted_volume_is_not_mounted():
    svc = make_service([make_volume()])
    result = svc.unmount("UUID-1", label="HD1")
    assert result.error.code == "not_mounted"


def test_eject_of_rw_volume_is_busy():
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    svc = make_service([rw])
    result = svc.eject("UUID-1", label="HD1")
    assert result.error.code == "busy"

def test_eject_of_read_only_volume_unmounts_then_ejects(monkeypatch):
    """Unmount restores Apple's mount, so Eject from `ro` tears it down first."""
    calls: dict[str, object] = {}

    def fake_unmount(volume, **kw):
        calls["keep_unmounted"] = kw["keep_unmounted"]
        return True

    def fake_eject(volume, **kw):
        calls["ejected"] = volume.device.label

    monkeypatch.setattr(svc_mod, "unmount_volume", fake_unmount)
    monkeypatch.setattr(svc_mod, "eject_volume", fake_eject)
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    result = svc.eject("UUID-1", label="HD1")
    assert result.ok is True
    assert calls == {"keep_unmounted": True, "ejected": "HD1"}

def test_eject_of_read_only_volume_busy_keeps_the_mount(monkeypatch):
    """A busy read-only volume is not ejected and no unmount ran."""
    exc = svc_mod.UnmountFailed("HD1 is busy.")

    def fake_unmount(volume, **kw):
        raise exc

    monkeypatch.setattr(svc_mod, "unmount_volume", fake_unmount)
    monkeypatch.setattr(
        svc_mod, "eject_volume", lambda *a, **k: pytest.fail("ejected anyway"))
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    result = svc.eject("UUID-1", label="HD1")
    assert result.error.code == "busy"


def test_unmount_reports_restore_failure(monkeypatch):
    monkeypatch.setattr(svc_mod, "unmount_volume", lambda *a, **k: False)
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    result = make_service([rw]).unmount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.restored is False
    assert result.error.code == "action_failed"


def test_unmount_success_restores_read_only(monkeypatch):
    monkeypatch.setattr(svc_mod, "unmount_volume", lambda *a, **k: True)
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    result = make_service([rw]).unmount("UUID-1", label="HD1")
    assert result.ok is True
    assert result.restored is True
    assert result.volume is not None


def test_eject_success_returns_no_volume(monkeypatch):
    monkeypatch.setattr(svc_mod, "eject_volume", lambda *a, **k: None)
    svc = make_service([make_volume()])
    result = svc.eject("UUID-1", label="HD1")
    assert result.ok is True
    assert result.volume is None
    assert result.restored is None


def test_eject_of_unlabeled_volume_reaches_unmounter(monkeypatch):
    calls = {}

    def fake_eject(volume, **kw):
        calls.update(kw)

    monkeypatch.setattr(svc_mod, "eject_volume", fake_eject)
    svc = make_service([make_volume(label="")])
    result = svc.eject("UUID-1", label="")
    assert result.ok is True
    assert calls  # the unmounter ran; the empty label was not refused


def test_mount_of_unlabeled_volume_is_refused(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume(label="")])
    result = svc.mount("UUID-1", label="")
    assert result.ok is False
    assert result.error.code == "invalid_request"
    assert result.error.status == 422


def test_mount_failure_dirty_maps_to_blocked_dirty(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("The volume is dirty. Apple's read-only mount was restored.")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.restored is True
    assert result.error.code == "blocked_dirty"
    assert result.error.status == 422


def test_mount_failure_busy_maps_to_busy(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("The device is busy.")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.error.code == "busy"
    assert result.error.status == 409


def test_mount_failure_permission_maps_to_permission_denied(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("ntfs-3g: permission denied opening /dev/disk4s1")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.error.code == "permission_denied"
    assert result.error.status == 403


def test_mount_failure_generic_maps_to_action_failed(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("ntfs-3g exited with status 1")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.error.code == "action_failed"
    assert result.error.status == 500
