import plistlib
from pathlib import Path

from ntfsmnt.diskutil import parse_info_plist

FIX = Path(__file__).parent / "fixtures" / "diskutil_info_hd1.plist"


def test_parses_hd1():
    d = parse_info_plist(FIX.read_bytes())
    assert d.identifier == "disk4s1"
    assert d.device == "/dev/disk4s1"
    assert d.label == "HD1"
    assert d.fs_type == "Windows_NTFS"
    assert d.mount_point == "/Volumes/HD1"
    assert d.size > 0
    assert d.uuid


def test_unmounted_volume_has_no_mount_point():
    info = plistlib.loads(FIX.read_bytes())
    del info["MountPoint"]
    d = parse_info_plist(plistlib.dumps(info))
    assert d.mount_point is None
    assert d.label == "HD1"
