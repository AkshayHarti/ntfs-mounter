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


def test_read_only_volume_offers_mount():
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    view = svc.snapshot().volumes[0]
    assert view.state == "ro"
    assert {a.name: a.enabled for a in view.actions} == {
        "mount": True, "unmount": False, "eject": False,
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
