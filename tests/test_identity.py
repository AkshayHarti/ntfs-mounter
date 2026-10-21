import re

import pytest

from ntfsmnt.identity import names_label, owner, parse_pid, pid_pattern, source_for
from ntfsmnt.mounttable import MountEntry

DEVICE = "/dev/disk4s1"

OURS = MountEntry(
    source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False
)
APPLE = MountEntry(
    source=DEVICE, mount_point="/Volumes/HD1", fs="ntfs", read_only=True
)
OTHER_LABEL = MountEntry(
    source="fuse-t:/HD2", mount_point="/Volumes/HD2", fs="nfs", read_only=False
)


class FakeRun:
    """Scripted `run`: records argv and returns pre-set `(rc, output)` pairs."""

    def __init__(self, *results):
        self.calls: list[list[str]] = []
        self.results = list(results)

    def __call__(self, argv):
        self.calls.append(list(argv))
        return self.results.pop(0) if self.results else (1, "")


def test_pid_pattern_matches_the_serving_process_and_its_argv_boundary():
    pattern = pid_pattern(DEVICE)

    assert pattern == "ntfs-3g .*/dev/disk4s1( |$)"
    assert re.search(pattern, f"ntfs-3g {DEVICE} /Volumes/HD1 -o uid=501")
    assert re.search(pattern, f"ntfs-3g -o uid=501 {DEVICE}")
    # A shorter device name must not match a longer one that starts with it.
    assert not re.search(pattern, "ntfs-3g /dev/disk4s11 /Volumes/HD1 -o uid=501")


def test_pid_pattern_escapes_the_device():
    assert pid_pattern("/dev/disk4s1") != pid_pattern("/dev/disk4s2")
    assert "\\." not in pid_pattern(DEVICE)  # nothing to escape here
    assert re.escape("/dev/disk4s1") in pid_pattern(DEVICE)


def test_parse_pid_takes_first_integer_token():
    assert parse_pid("4242\n") == 4242
    assert parse_pid("pgrep: some warning\n4242\n") == 4242
    assert parse_pid("no such process") is None
    assert parse_pid("") is None


def test_names_label_accepts_only_the_volumes_own_fuse_t_source():
    assert source_for("HD1") == "fuse-t:/HD1"
    assert names_label(OURS, "HD1") is True
    assert names_label(OURS, "HD2") is False
    assert names_label(OTHER_LABEL, "HD1") is False
    # A label is the entry's identity; an unlabeled volume cannot claim one.
    assert names_label(OURS, "") is False
    # Apple's mount names a device, not a label.
    assert names_label(APPLE, "HD1") is False


def owner_for(results, *, label_shared=False, entry=OURS):
    return owner(
        entry,
        device=DEVICE,
        label="HD1",
        run=FakeRun(*results),
        label_shared=label_shared,
    )


def test_owner_is_other_for_an_entry_carrying_another_label():
    # fuse-t:/HD2 is another volume's mount, however unique the label HD1 is
    # and whatever pgrep would say about disk4s1. Asking would be a guess.
    run = FakeRun((0, "4242\n"))

    verdict = owner(OTHER_LABEL, device=DEVICE, label="HD1", run=run)

    assert verdict == "other"
    assert run.calls == []


def test_owner_is_other_for_apple_mounts_without_asking_pgrep():
    run = FakeRun((0, "4242\n"))

    assert owner(APPLE, device=DEVICE, label="HD1", run=run) == "other"
    assert run.calls == []


def test_owner_is_ours_when_the_process_names_the_device():
    assert owner_for([(0, "4242\n")], label_shared=True) == "ours"


def test_owner_treats_a_unique_label_as_ownership_when_pgrep_finds_nothing():
    # One volume carries HD1: no other disk can own fuse-t:/HD1.
    assert owner_for([(1, "")]) == "ours"


def test_owner_trusts_pgrep_when_the_label_is_shared():
    assert owner_for([(1, "")], label_shared=True) == "other"


@pytest.mark.parametrize("label_shared", [False, True])
def test_owner_is_unknown_when_pgrep_cannot_run(label_shared):
    assert (
        owner_for([(127, "pgrep: command not found")], label_shared=label_shared)
        == "unknown"
    )


def test_owner_is_other_for_apple_mounts():
    assert owner_for([], entry=APPLE) == "other"
