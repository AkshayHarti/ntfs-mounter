import pytest

from ntfsmnt.models import Device
from ntfsmnt.mounttable import parse_mount_table
from ntfsmnt.volumes import AmbiguousVolume, NoSuchVolume, build_volumes, resolve


def dev(ident, label, uuid="U1", mount_point=None):
    return Device(
        identifier=ident,
        device=f"/dev/{ident}",
        label=label,
        uuid=uuid,
        size=1,
        fs_type="Windows_NTFS",
        mount_point=mount_point,
    )


def test_state_ro_unmounted_rw():
    mounts = parse_mount_table("fuse-t:/HD1 on /Volumes/HD1 (nfs)\n")
    vols = build_volumes([dev("disk4s1", "HD1"), dev("disk4s2", "HD2")], mounts)
    assert {v.device.identifier: v.state for v in vols} == {
        "disk4s1": "rw",
        "disk4s2": "unmounted",
    }


def test_state_ro_for_apple_mount():
    mounts = parse_mount_table(
        "/dev/disk4s1 on /Volumes/HD1 (ntfs, local, nodev, nosuid, read-only, fskit)\n"
    )
    vols = build_volumes([dev("disk4s1", "HD1")], mounts)
    assert [v.state for v in vols] == ["ro"]
    assert vols[0].mount_point == "/Volumes/HD1"


def test_association_by_device_mount_point():
    mounts = parse_mount_table("/dev/disk9s1 on /Volumes/Other (ntfs, read-only)\n")
    vols = build_volumes([dev("disk4s1", "HD1", mount_point="/Volumes/Other")], mounts)
    assert [m.mount_point for m in vols[0].mounts] == ["/Volumes/Other"]


def test_duplicate_label_is_ambiguous():
    vols = build_volumes([dev("disk4s1", "DATA", "U1"), dev("disk6s1", "DATA", "U2")], [])
    with pytest.raises(AmbiguousVolume) as e:
        resolve("DATA", vols)
    assert {c.device.identifier for c in e.value.candidates} == {"disk4s1", "disk6s1"}


def test_resolve_by_uuid_and_device():
    vols = build_volumes([dev("disk4s1", "HD1", "ABC")], [])
    assert resolve("ABC", vols).device.identifier == "disk4s1"
    assert resolve("/dev/disk4s1", vols).device.identifier == "disk4s1"


def test_resolve_by_mount_point():
    mounts = parse_mount_table("fuse-t:/HD1 on /Volumes/HD1 (nfs)\n")
    vols = build_volumes([dev("disk4s1", "HD1")], mounts)
    assert resolve("/Volumes/HD1", vols).device.identifier == "disk4s1"


def test_unknown_volume():
    with pytest.raises(NoSuchVolume):
        resolve("NOPE", [])
