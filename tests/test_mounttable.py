from pathlib import Path

from ntfsmnt import mounttable
from ntfsmnt.mounttable import entry_for, parse_mount_table, read_mount_table

FIX = Path(__file__).parent / "fixtures"


def test_apple_ro_line():
    e = parse_mount_table("/dev/disk4s1 on /Volumes/HD1 (ntfs, local, read-only, fskit)\n")[0]
    assert (e.source, e.mount_point, e.fs, e.read_only) == (
        "/dev/disk4s1",
        "/Volumes/HD1",
        "ntfs",
        True,
    )


def test_fuset_line_is_not_ro():
    e = parse_mount_table("fuse-t:/HD1 on /Volumes/HD1 (nfs, nodev)\n")[0]
    assert e.source == "fuse-t:/HD1" and e.fs == "nfs" and e.read_only is False


def test_mount_point_with_space():
    e = parse_mount_table("/dev/disk9s1 on /Volumes/My Drive (ntfs, read-only)\n")[0]
    assert e.mount_point == "/Volumes/My Drive"


def test_parses_ro_fixture():
    e = parse_mount_table((FIX / "mount_ro.txt").read_text())[0]
    assert e == mounttable.MountEntry("/dev/disk4s1", "/Volumes/HD1", "ntfs", True)


def test_parses_fuset_fixture():
    e = parse_mount_table((FIX / "mount_fuset.txt").read_text())[0]
    assert e == mounttable.MountEntry("fuse-t:/HD1", "/Volumes/HD1", "nfs", False)


def test_skips_blank_lines_and_preserves_order():
    text = "\n" + (FIX / "mount_ro.txt").read_text() + "\n" + (FIX / "mount_fuset.txt").read_text()
    sources = [e.source for e in parse_mount_table(text)]
    assert sources == ["/dev/disk4s1", "fuse-t:/HD1"]


def test_entry_for_returns_first_match_or_none():
    entries = parse_mount_table((FIX / "mount_fuset.txt").read_text())
    assert entry_for(entries, "/Volumes/HD1") is entries[0]
    assert entry_for(entries, "/Volumes/Nope") is None


def test_read_mount_table_parses_command_output(monkeypatch):
    monkeypatch.setattr(
        mounttable.subprocess,
        "check_output",
        lambda cmd, **kwargs: (FIX / "mount_fuset.txt").read_text(),
    )
    assert read_mount_table()[0].source == "fuse-t:/HD1"
