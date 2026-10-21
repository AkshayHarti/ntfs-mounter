import re

import pytest

from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.unmounter import (
    UnmountFailed,
    eject_volume,
    has_mount,
    unmount_volume,
)
from ntfsmnt.volumes import Volume, build_volumes

DEVICE = Device(
    identifier="disk4s1",
    device="/dev/disk4s1",
    label="HD1",
    uuid="UUID-HD1",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
# Same label, different disk: the review's way to tell an identity bug apart
# from a working one.
OTHER = Device(
    identifier="disk6s1",
    device="/dev/disk6s1",
    label="HD1",
    uuid="UUID-HD1-OTHER",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
MOUNT_POINT = "/Volumes/HD1"
NTFS3G = "/usr/local/bin/ntfs-3g"

OURS = MountEntry(
    source="fuse-t:/HD1", mount_point=MOUNT_POINT, fs="nfs", read_only=False
)
APPLE = MountEntry(
    source="/dev/disk4s1", mount_point=MOUNT_POINT, fs="ntfs", read_only=True
)


def serve(device, mount_point):
    return [NTFS3G, device.device, mount_point, "-o", f"volname={device.label}"]


class FakeEnv:
    """Scripted stand-in for the injected `run`, `mounts`, and `sleep`.

    The process table and `pgrep` are modelled: `pgrep` matches its pattern
    against each recorded ntfs-3g command line, so identity has to be earned.
    """

    def __init__(self, entries=(), serving=None):
        self.calls: list[list[str]] = []
        self.entries: list[MountEntry] = list(entries)
        if serving is not None:
            self.procs: list[list[str]] = [list(proc) for proc in serving]
        elif self._has_fuse_t():
            # A FUSE-T entry in the table implies its serving process exists.
            self.procs = [serve(DEVICE, MOUNT_POINT)]
        else:
            self.procs = []
        self.points: dict[str, str] = {
            DEVICE.device: MOUNT_POINT,
            OTHER.device: MOUNT_POINT,
        }
        self.sent_signals: list[str] = []
        self.slept: list[float] = []
        self._diskutil_unmount = (0, "")
        self._diskutil_mount = (0, "")
        self._umount_results: list[tuple[int, str]] = []
        self._eject = (0, "")
        self._pgrep: tuple[int, str] | None = None

    def _has_fuse_t(self):
        return any(
            e.fs == "nfs" and e.source.startswith("fuse-t:") for e in self.entries
        )

    # --- injected callables -------------------------------------------------
    def run(self, argv):
        self.calls.append(list(argv))
        if argv[:2] == ["diskutil", "unmount"]:
            returncode, stderr = self._diskutil_unmount
            if returncode == 0:
                self._remove(argv[2])
            return returncode, stderr
        if argv[0] == "umount":
            returncode, stderr = (
                self._umount_results.pop(0) if self._umount_results else (0, "")
            )
            if returncode == 0:
                self._remove(argv[-1])
            return returncode, stderr
        if argv[:2] == ["diskutil", "mount"]:
            returncode, stderr = self._diskutil_mount
            if returncode == 0:
                self.entries.append(
                    MountEntry(
                        source=argv[2],
                        mount_point=self.points.get(argv[2], MOUNT_POINT),
                        fs="ntfs",
                        read_only=True,
                    )
                )
            return returncode, stderr
        if argv[:2] == ["diskutil", "eject"]:
            return self._eject
        if argv[0] == "pgrep":
            if self._pgrep is not None:
                return self._pgrep
            pattern = argv[2]
            if any(re.search(pattern, " ".join(proc)) for proc in self.procs):
                return 0, "4242\n"
            return 1, ""
        if argv[0] == "kill":
            self.sent_signals.append({"-TERM": "SIGTERM", "-9": "SIGKILL"}[argv[1]])
            return 0, ""
        raise AssertionError(f"unexpected command: {argv}")

    def mounts(self):
        return list(self.entries)

    def sleep(self, seconds):
        self.slept.append(seconds)

    # --- scripting ----------------------------------------------------------
    def unmount_returns(self, returncode, output=""):
        self._diskutil_unmount = (returncode, output)

    def mount_returns(self, returncode, output=""):
        self._diskutil_mount = (returncode, output)

    def umount_returns(self, *results):
        self._umount_results = list(results)

    def eject_returns(self, returncode, output=""):
        self._eject = (returncode, output)

    def pgrep_returns(self, returncode, output=""):
        self._pgrep = (returncode, output)

    # --- assertions helpers -------------------------------------------------
    def _remove(self, mount_point):
        self.entries = [e for e in self.entries if e.mount_point != mount_point]
        self.procs = [p for p in self.procs if p[2] != mount_point]

    def volume(self):
        return Volume(device=DEVICE, mounts=tuple(self.entries))

    def state(self):
        return self.volume().state

    def diskutil_calls(self):
        return [call for call in self.calls if call[0] == "diskutil"]

    def commands(self):
        return [call[0] for call in self.calls]

    def signals_in_argv(self):
        return [call for call in self.calls if "KILL" in " ".join(call) or "-9" in call]


def test_unmount_restores_read_only_by_default():
    env = FakeEnv([OURS])

    assert (
        unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)
        is True
    )

    assert env.state() == "ro"
    assert env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_unmount_restore_failure_returns_false():
    env = FakeEnv([OURS])
    env.mount_returns(1, "Could not mount disk4s1")

    restored = unmount_volume(
        env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep
    )

    assert restored is False
    assert env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert env.state() == "unmounted"


def test_restore_is_attempted_when_a_later_mount_point_fails():
    apple_remount = MountEntry(
        source="/dev/disk4s1", mount_point="/Volumes/HD1 1", fs="ntfs", read_only=True
    )
    env = FakeEnv([OURS, apple_remount])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((0, ""), (1, "Resource busy"))
    env.pgrep_returns(1, "")

    with pytest.raises(UnmountFailed):
        unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert ["diskutil", "unmount", "/Volumes/HD1"] in env.calls
    assert env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_keep_unmounted_returns_true_without_restoring():
    env = FakeEnv([OURS])

    restored = unmount_volume(
        env.volume(),
        run=env.run,
        mounts=env.mounts,
        sleep=env.sleep,
        keep_unmounted=True,
    )

    assert restored is True
    assert env.state() == "unmounted"
    assert not any(call[:2] == ["diskutil", "mount"] for call in env.calls)


def test_unmount_falls_back_to_plain_umount_then_restores():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Unmount failed")

    unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.commands()[-3:] == ["diskutil", "umount", "diskutil"]
    assert env.state() == "ro"
    assert env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_unmount_evicts_apple_mount_when_that_is_all_that_is_mounted():
    env = FakeEnv([APPLE])

    unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.calls[0] == ["diskutil", "unmount", MOUNT_POINT]
    assert env.state() == "ro"


@pytest.mark.parametrize("source", ["fuse-t:/HD1", "fuse-t:/HD1-old"])
def test_live_mount_is_found_when_pgrep_confirms_the_device(source):
    # The snapshot is stale: it knows of no mount, but FUSE-T still serves
    # /Volumes/HD1 (and may still carry a label the disk no longer has).
    stale = MountEntry(
        source=source, mount_point=MOUNT_POINT, fs="nfs", read_only=False
    )
    env = FakeEnv([stale])
    volume = Volume(device=DEVICE, mounts=())
    assert volume.mount_point is None

    unmount_volume(
        volume,
        run=env.run,
        mounts=env.mounts,
        sleep=env.sleep,
        keep_unmounted=True,
    )

    assert ["diskutil", "unmount", MOUNT_POINT] in env.calls
    assert ["pgrep", "-f", "ntfs-3g .*/dev/disk4s1( |$)"] in env.calls
    assert env.state() == "unmounted"


def test_unmount_of_another_volume_with_the_same_label_refuses_and_leaves_a_alone():
    # Drive A (disk4s1) is read-write at /Volumes/HD1. Drive B (disk6s1,
    # unmounted) shares the label, so `build_volumes` attributes A's mount to
    # B as well. Unmounting B must neither tear down A's mount nor guess.
    env = FakeEnv([OURS])
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_b = next(v for v in volumes if v.device is OTHER)

    assert drive_b.state == "rw"
    assert drive_b.mount_point == MOUNT_POINT

    with pytest.raises(UnmountFailed) as excinfo:
        unmount_volume(
            drive_b,
            run=env.run,
            mounts=env.mounts,
            sleep=env.sleep,
            label_shared=True,
        )

    message = str(excinfo.value)
    assert MOUNT_POINT in message
    assert "another volume" in message
    assert "same label" in message
    assert "manually" in message
    assert not [call for call in env.calls if call[0] in ("diskutil", "umount", "kill")]
    assert env.entries == [OURS]


def test_unmount_of_the_serving_volume_unpacks_its_own_mount():
    env = FakeEnv([OURS])
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_a = next(v for v in volumes if v.device is DEVICE)

    restored = unmount_volume(
        drive_a,
        run=env.run,
        mounts=env.mounts,
        sleep=env.sleep,
        label_shared=True,
    )

    assert restored is True
    assert ["pgrep", "-f", "ntfs-3g .*/dev/disk4s1( |$)"] in env.calls
    assert ["diskutil", "unmount", MOUNT_POINT] in env.calls
    assert env.state() == "ro"


def test_unmount_with_a_unique_label_proceeds_when_pgrep_cannot_answer():
    # One volume carries the label: the entry can only be its mount, so a
    # missing pgrep is a stale process table, not another disk.
    env = FakeEnv([OURS])
    env.pgrep_returns(127, "pgrep: command not found")
    volume = build_volumes([DEVICE], env.mounts())[0]

    restored = unmount_volume(volume, run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert restored is True
    assert ["diskutil", "unmount", MOUNT_POINT] in env.calls
    assert env.state() == "ro"


def test_unmount_with_a_shared_label_refuses_when_pgrep_cannot_answer():
    env = FakeEnv([OURS])
    env.pgrep_returns(127, "pgrep: command not found")
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_a = next(v for v in volumes if v.device is DEVICE)

    with pytest.raises(UnmountFailed) as excinfo:
        unmount_volume(
            drive_a,
            run=env.run,
            mounts=env.mounts,
            sleep=env.sleep,
            label_shared=True,
        )

    assert "manually" in str(excinfo.value)
    assert not [call for call in env.calls if call[0] in ("diskutil", "umount", "kill")]
    assert env.entries == [OURS]


def test_busy_unmount_skips_the_restore_while_our_mount_is_still_live():
    # `diskutil mount` under a live read-write mount is refused or lands at
    # "/Volumes/HD1 1"; neither may happen.
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((1, "Resource busy"), (1, "Resource busy"))

    with pytest.raises(UnmountFailed):
        unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.entries == [OURS]
    assert not [call for call in env.calls if call[:2] == ["diskutil", "mount"]]
    assert not any(e.mount_point == f"{MOUNT_POINT} 1" for e in env.entries)


def test_unmount_refuses_busy_and_names_mount():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((1, "Resource busy"), (1, "umount: Resource busy"))

    with pytest.raises(UnmountFailed) as excinfo:
        unmount_volume(
            env.volume(),
            run=env.run,
            mounts=env.mounts,
            sleep=env.sleep,
            keep_unmounted=True,
        )

    message = str(excinfo.value)
    assert MOUNT_POINT in message
    assert "close" in message.lower()
    assert "Resource busy" in message


def test_unmount_uses_sigterm_never_sigkill():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((1, "Resource busy"), (1, "Resource busy"))

    with pytest.raises(UnmountFailed):
        unmount_volume(
            env.volume(),
            run=env.run,
            mounts=env.mounts,
            sleep=env.sleep,
            keep_unmounted=True,
        )

    assert env.sent_signals == ["SIGTERM"]
    assert env.signals_in_argv() == []
    assert ["pgrep", "-f", "ntfs-3g .*/dev/disk4s1( |$)"] in env.calls
    assert ["kill", "-TERM", "4242"] in env.calls
    assert env.slept == [1]


def test_busy_without_a_pid_still_reports_busy_and_never_signals():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((1, "Resource busy"))
    env.pgrep_returns(1, "")

    with pytest.raises(UnmountFailed) as excinfo:
        unmount_volume(
            env.volume(),
            run=env.run,
            mounts=env.mounts,
            sleep=env.sleep,
            keep_unmounted=True,
        )

    message = str(excinfo.value)
    assert MOUNT_POINT in message
    assert "close" in message.lower()
    assert "Resource busy" in message
    assert env.sent_signals == []
    assert not any(call[0] == "kill" for call in env.calls)
    assert env.calls.count(["umount", MOUNT_POINT]) == 1


def test_unmount_retries_once_after_sigterm_and_succeeds():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Resource busy")
    env.umount_returns((1, "Resource busy"), (0, ""))

    unmount_volume(
        env.volume(),
        run=env.run,
        mounts=env.mounts,
        sleep=env.sleep,
        keep_unmounted=True,
    )

    assert env.sent_signals == ["SIGTERM"]
    assert env.calls.count(["umount", MOUNT_POINT]) == 2
    assert env.state() == "unmounted"


def test_unmount_reports_first_stderr_line_for_other_failures():
    env = FakeEnv([OURS])
    env.unmount_returns(1, "Volume is not mounted")
    env.umount_returns((1, "Volume is not mounted"))

    with pytest.raises(UnmountFailed) as excinfo:
        unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert str(excinfo.value) == "Volume is not mounted"


def test_has_mount_is_false_for_a_same_label_entry_served_by_another_disk():
    env = FakeEnv([OURS])
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_b = next(v for v in volumes if v.device is OTHER)

    assert (
        has_mount(
            drive_b, run=env.run, mounts=env.mounts, label_shared=True
        )
        is False
    )


def test_has_mount_is_true_when_the_serving_process_names_the_device():
    stale = MountEntry(
        source="fuse-t:/HD1", mount_point=MOUNT_POINT, fs="nfs", read_only=False
    )
    env = FakeEnv([stale])
    volume = Volume(device=DEVICE, mounts=())
    assert has_mount(volume, run=env.run, mounts=env.mounts) is True


def test_has_mount_is_true_for_an_apple_read_only_mount():
    env = FakeEnv([APPLE])
    volume = Volume(device=DEVICE, mounts=())
    assert has_mount(volume, run=env.run, mounts=env.mounts) is True


def test_has_mount_is_false_when_nothing_is_mounted():
    env = FakeEnv()
    volume = Volume(device=DEVICE, mounts=())
    assert has_mount(volume, run=env.run, mounts=env.mounts) is False


def test_eject_refuses_while_mounted():
    env = FakeEnv([OURS])

    with pytest.raises(UnmountFailed) as excinfo:
        eject_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert "unmount" in str(excinfo.value).lower()
    assert not any(call[:2] == ["diskutil", "eject"] for call in env.calls)


def test_eject_ignores_a_same_label_mount_served_by_another_disk():
    env = FakeEnv([OURS])
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_b = next(v for v in volumes if v.device is OTHER)

    eject_volume(
        drive_b, run=env.run, mounts=env.mounts, sleep=env.sleep, label_shared=True
    )

    assert ["diskutil", "eject", "disk6"] in env.calls


def test_eject_refuses_when_a_shared_label_cannot_be_resolved():
    env = FakeEnv([OURS])
    env.pgrep_returns(127, "pgrep: command not found")
    volumes = build_volumes([DEVICE, OTHER], env.mounts())
    drive_b = next(v for v in volumes if v.device is OTHER)

    with pytest.raises(UnmountFailed):
        eject_volume(
            drive_b, run=env.run, mounts=env.mounts, sleep=env.sleep, label_shared=True
        )

    assert not any(call[:2] == ["diskutil", "eject"] for call in env.calls)


def test_eject_when_unmounted_uses_parent_disk_once():
    env = FakeEnv()

    eject_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.calls == [["diskutil", "eject", "disk4"]]


def test_eject_failure_reports_first_stderr_line():
    env = FakeEnv()
    env.eject_returns(1, "Could not eject disk4\nTry again")

    with pytest.raises(UnmountFailed) as excinfo:
        eject_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert str(excinfo.value) == "Could not eject disk4"


def test_already_unmounted_makes_no_calls():
    env = FakeEnv()

    unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.diskutil_calls() == []
