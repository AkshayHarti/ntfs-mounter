import pytest

from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.unmounter import UnmountFailed, eject_volume, unmount_volume
from ntfsmnt.volumes import Volume

DEVICE = Device(
    identifier="disk4s1",
    device="/dev/disk4s1",
    label="HD1",
    uuid="UUID-HD1",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
MOUNT_POINT = "/Volumes/HD1"

OURS = MountEntry(
    source="fuse-t:/HD1", mount_point=MOUNT_POINT, fs="nfs", read_only=False
)
APPLE = MountEntry(
    source="/dev/disk4s1", mount_point=MOUNT_POINT, fs="ntfs", read_only=True
)


class FakeEnv:
    """Scripted stand-in for the injected `run`, `mounts`, and `sleep`."""

    def __init__(self, entries=()):
        self.calls: list[list[str]] = []
        self.entries: list[MountEntry] = list(entries)
        self.sent_signals: list[str] = []
        self.slept: list[float] = []
        self._diskutil_unmount = (0, "")
        self._umount_results: list[tuple[int, str]] = []
        self._eject = (0, "")
        self._pgrep_output = "4242\n"

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
            self.entries.append(APPLE)
            return 0, ""
        if argv[:2] == ["diskutil", "eject"]:
            return self._eject
        if argv[0] == "pgrep":
            return 0, self._pgrep_output
        if argv[0] == "kill":
            self.sent_signals.append({"-TERM": "SIGTERM", "-9": "SIGKILL"}[argv[1]])
            return 0, ""
        raise AssertionError(f"unexpected command: {argv}")

    def mounts(self):
        return list(self.entries)

    def sleep(self, seconds):
        self.slept.append(seconds)

    # --- scripting ----------------------------------------------------------
    def unmount_returns(self, returncode, stderr=""):
        self._diskutil_unmount = (returncode, stderr)

    def umount_returns(self, *results):
        self._umount_results = list(results)

    def eject_returns(self, returncode, stderr=""):
        self._eject = (returncode, stderr)

    # --- assertions helpers -------------------------------------------------
    def _remove(self, mount_point):
        self.entries = [e for e in self.entries if e.mount_point != mount_point]

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

    unmount_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert env.state() == "ro"
    assert env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_keep_unmounted_skips_restore():
    env = FakeEnv([OURS])

    unmount_volume(
        env.volume(),
        run=env.run,
        mounts=env.mounts,
        sleep=env.sleep,
        keep_unmounted=True,
    )

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
    assert ["kill", "-TERM", "4242"] in env.calls
    assert env.slept == [1]


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


def test_eject_refuses_while_mounted():
    env = FakeEnv([OURS])

    with pytest.raises(UnmountFailed) as excinfo:
        eject_volume(env.volume(), run=env.run, mounts=env.mounts, sleep=env.sleep)

    assert "unmount" in str(excinfo.value).lower()
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
