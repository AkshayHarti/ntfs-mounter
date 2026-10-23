import plistlib
from pathlib import Path

from ntfsmnt.diskutil import parse_info_plist, parse_list_plist

FIX = Path(__file__).parent / "fixtures" / "diskutil_info_hd1.plist"
FIX_LIST = Path(__file__).parent / "fixtures" / "diskutil_list.plist"


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


def test_parse_list_plist():
    # Captured on this machine with `diskutil list -plist`. disk4 (HD1, HD2) and
    # an attached USB disk5 (three NTFS volumes) were present at capture time;
    # every Windows_NTFS partition is listed in file order, whole disks are not.
    assert parse_list_plist(FIX_LIST.read_bytes()) == [
        "disk4s1",
        "disk4s2",
        "disk5s1",
        "disk5s2",
        "disk5s3",
    ]


def test_parse_list_plist_ignores_non_ntfs_and_whole_disks():
    listing = {
        "AllDisksAndPartitions": [
            {
                "Content": "windows_ntfs",
                "DeviceIdentifier": "disk9",
                "Partitions": [
                    {"Content": "Apple_APFS", "DeviceIdentifier": "disk9s1"},
                    {"Content": "windows_ntfs", "DeviceIdentifier": "disk9s2"},
                ],
            }
        ]
    }
    assert parse_list_plist(plistlib.dumps(listing)) == ["disk9s2"]
