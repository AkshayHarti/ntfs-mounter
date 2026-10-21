import pytest

from ntfsmnt import mounter
from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.mounter import MountFailed, _parse_pid, _write_probe, mount_volume
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
NTFS3G = "/usr/local/bin/ntfs-3g"

APPLE_MOUNT = MountEntry(
    source="/dev/disk4s1", mount_point=MOUNT_POINT, fs="ntfs", read_only=True
)
OURS = MountEntry(
    source="fuse-t:/HD1", mount_point=MOUNT_POINT, fs="nfs", read_only=False
)
# Apple's FSKit remounts the device read-only, next to ours when the name is taken.
APPLE_REMOUNT = MountEntry(
    source="/dev/disk4s1", mount_point="/Volumes/HD1 1", fs="ntfs", read_only=True
)


class FakeEnv:
    """Scripted stand-in for the injected `run`, `mounts`, `sleep`, and probe."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.entries: list[MountEntry] = []
        self.ntfs3g_calls = 0
        self.evictions = 0
        self.write_probe_called = False
        self.slept: list[float] = []
        self._ntfs3g_rc = 0
        self._stderr = ""
        self._apple_returns = False
        self._apple_returned = False
        self._device_present = True
        self._probe_error: OSError | None = None
        self._diskutil_unmount_rc = 0

    # --- injected callables -------------------------------------------------
    def run(self, argv):
        self.calls.append(list(argv))
        if argv[0] == "test":
            return (0 if self._device_present else 1, "")
        if argv[0] == NTFS3G:
            self.ntfs3g_calls += 1
            if self._ntfs3g_rc == 0:
                self.entries = [OURS]
            return (self._ntfs3g_rc, self._stderr)
        if argv[:2] == ["diskutil", "unmount"]:
            if self.ntfs3g_calls and argv[2] != MOUNT_POINT:
                self.evictions += 1
            if self._diskutil_unmount_rc != 0:
                return (self._diskutil_unmount_rc, "Resource busy")
            self._remove(argv[2])
            return (0, "")
        if argv[0] == "umount":
            self._remove(argv[2])
            return (0, "")
        if argv[:2] == ["diskutil", "mount"]:
            self.entries.append(APPLE_MOUNT)
            return (0, "")
        if argv[0] == "pgrep":
            return (0, "4242\n")
        raise AssertionError(f"unexpected command: {argv}")

    def mounts(self):
        if self.ntfs3g_calls and self._apple_returns and not self._apple_returned:
            self._apple_returned = True
            self.entries.append(APPLE_REMOUNT)
        return list(self.entries)

    def sleep(self, seconds):
        self.slept.append(seconds)

    def probe(self, mount_point):
        assert mount_point == MOUNT_POINT
        self.write_probe_called = True
        if self._probe_error is not None:
            raise self._probe_error

    # --- helpers ------------------------------------------------------------
    def _remove(self, mount_point):
        self.entries = [e for e in self.entries if e.mount_point != mount_point]

    def ntfs3g_invocations(self):
        return sum(1 for call in self.calls if call[0] == NTFS3G)

    def state(self, label):
        if any(e.fs == "nfs" and e.source == f"fuse-t:/{label}" for e in self.entries):
            return "rw"
        return "ro" if self.entries else "unmounted"

    def mount(
        self,
        *,
        state="unmounted",
        ntfs3g_rc=0,
        stderr="",
        apple_returns=False,
        device_present=True,
        probe_error=None,
        diskutil_unmount_rc=0,
        timeout=30,
    ):
        self.entries = {"ro": [APPLE_MOUNT], "rw": [OURS], "unmounted": []}[state]
        self._ntfs3g_rc = ntfs3g_rc
        self._stderr = stderr
        self._apple_returns = apple_returns
        self._device_present = device_present
        self._probe_error = probe_error
        self._diskutil_unmount_rc = diskutil_unmount_rc
        return mount_volume(
            Volume(device=DEVICE, mounts=tuple(self.entries)),
            uid=501,
            gid=20,
            run=self.run,
            mounts=self.mounts,
            sleep=self.sleep,
            timeout=timeout,
            probe=self.probe,
        )


@pytest.fixture(autouse=True)
def mount_point_dirs(monkeypatch):
    """Keep `mkdir` off the host: no test may create anything under /Volumes."""
    created: list[str] = []
    monkeypatch.setattr(mounter, "_ensure_mount_point", created.append)
    return created


@pytest.fixture
def fake_env():
    return FakeEnv()


def test_idempotent_when_already_rw(fake_env):
    result = fake_env.mount(state="rw")

    assert result.pid is None
    assert result.mount_point == MOUNT_POINT
    assert fake_env.ntfs3g_invocations() == 0
    assert not [c for c in fake_env.calls if c[:2] == ["diskutil", "unmount"]]


def test_evicts_competitor_before_mounting(fake_env):
    fake_env.mount(state="ro")

    unmount_indexes = [
        i
        for i, call in enumerate(fake_env.calls)
        if call[:2] == ["diskutil", "unmount"]
    ]
    ntfs3g_indexes = [i for i, call in enumerate(fake_env.calls) if call[0] == NTFS3G]

    assert unmount_indexes, "expected Apple's mount to be unmounted first"
    assert ntfs3g_indexes
    assert unmount_indexes[0] < ntfs3g_indexes[0]
    assert fake_env.calls[unmount_indexes[0]][2] == MOUNT_POINT


def test_failed_diskutil_unmount_falls_back_to_umount(fake_env):
    fake_env.mount(state="ro", diskutil_unmount_rc=1)

    umount_indexes = [
        i for i, call in enumerate(fake_env.calls) if call[:2] == ["umount", "-f"]
    ]
    ntfs3g_indexes = [i for i, call in enumerate(fake_env.calls) if call[0] == NTFS3G]

    assert umount_indexes
    assert fake_env.calls[umount_indexes[0]] == ["umount", "-f", MOUNT_POINT]
    assert umount_indexes[0] < ntfs3g_indexes[0]


def test_live_table_wins_over_stale_volume_snapshot(fake_env):
    fake_env.entries = [APPLE_MOUNT]

    result = mount_volume(
        Volume(device=DEVICE, mounts=(OURS,)),  # stale: claims read-write already
        uid=501,
        gid=20,
        run=fake_env.run,
        mounts=fake_env.mounts,
        sleep=fake_env.sleep,
        probe=fake_env.probe,
    )

    assert fake_env.ntfs3g_invocations() == 1
    assert result.pid == 4242
    assert fake_env.state("HD1") == "rw"


def test_remounts_and_evicts_apple_after_rw(fake_env, mount_point_dirs):
    result = fake_env.mount(state="ro", apple_returns=True)

    assert mount_point_dirs == [MOUNT_POINT]
    assert fake_env.evictions >= 1
    assert fake_env.write_probe_called
    assert fake_env.state("HD1") == "rw"
    assert result.pid == 4242


def test_failure_restores_read_only(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="unmounted", ntfs3g_rc=14, stderr="Volume is hibernated")

    assert "--remove-hiberfile" in str(err.value)
    assert not fake_env.write_probe_called
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_probe_failure_restores_read_only(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro", probe_error=OSError(30, "Read-only file system"))

    message = str(err.value).lower()
    assert "mounted" in message
    assert "not writable" in message
    assert [c for c in fake_env.calls if c == ["diskutil", "unmount", MOUNT_POINT]]
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_device_never_appears_raises_without_mounting(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro", device_present=False)

    assert "/dev/disk4s1" in str(err.value)
    assert fake_env.ntfs3g_invocations() == 0
    assert len([c for c in fake_env.calls if c[0] == "test"]) == 10
    assert fake_env.slept == [1] * 9
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_mount_point_creation_failure_is_mount_failed(fake_env, monkeypatch):
    def deny(path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(mounter, "_ensure_mount_point", deny)

    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="unmounted")

    message = str(err.value)
    assert MOUNT_POINT in message
    assert "Permission denied" in message
    assert fake_env.ntfs3g_invocations() == 0


def test_parse_pid_takes_first_integer_token():
    assert _parse_pid("4242\n") == 4242
    assert _parse_pid("pgrep: some warning\n4242\n") == 4242
    assert _parse_pid("no such process") is None


def test_real_probe_writes_reads_back_and_cleans_up(tmp_path):
    _write_probe(str(tmp_path))

    assert not (tmp_path / ".ntfsmnt-probe").exists()


def test_real_probe_raises_oserror_when_it_cannot_write(tmp_path):
    with pytest.raises(OSError):
        _write_probe(str(tmp_path / "missing"))
