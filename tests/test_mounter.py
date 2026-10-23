import re
from pathlib import Path
from subprocess import CalledProcessError

import pytest

from ntfsmnt import mounter
from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.mounter import MountFailed, _write_probe, mount_volume
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
OTHER = Device(
    identifier="disk6s1",
    device="/dev/disk6s1",
    label="DATA",
    uuid="UUID-DATA",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
# A second disk carrying OTHER's label: the case where a label stops being an
# identity. It is the one that may already hold /Volumes/DATA read-write.
DATA_DISK = Device(
    identifier="disk4s1",
    device="/dev/disk4s1",
    label="DATA",
    uuid="UUID-DATA-A",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
MOUNT_POINT = "/Volumes/HD1"
OTHER_MOUNT_POINT = "/Volumes/DATA"
# A second disk with a label of its own: its entry may never stand in for HD1's.
HD2 = Device(
    identifier="disk6s1",
    device="/dev/disk6s1",
    label="HD2",
    uuid="UUID-HD2",
    size=1,
    fs_type="Windows_NTFS",
    mount_point=None,
)
HD2_MOUNT_POINT = "/Volumes/HD2"
NTFS3G = "/usr/local/bin/ntfs-3g"


def ro_entry(device, mount_point):
    return MountEntry(
        source=device.device, mount_point=mount_point, fs="ntfs", read_only=True
    )


def rw_entry(device, mount_point):
    label = device.label
    return MountEntry(
        source=f"fuse-t:/{label}", mount_point=mount_point, fs="nfs", read_only=False
    )


APPLE_MOUNT = ro_entry(DEVICE, MOUNT_POINT)
OURS = rw_entry(DEVICE, MOUNT_POINT)
# Apple's FSKit remounts the device read-only, next to ours when the name is taken.
APPLE_REMOUNT = ro_entry(DEVICE, f"{MOUNT_POINT} 1")


class FakeEnv:
    """A scripted host: one mount table, the live ntfs-3g processes behind it,
    and a `run` that models the commands against both.

    `pgrep` is answered by matching its pattern against the command line of the
    recorded processes, and an ntfs-3g invocation that succeeds leaves both a
    mount entry and a serving process behind — the real contract.
    """

    def __init__(self, mount_point=MOUNT_POINT):
        self.calls: list[list[str]] = []
        self.table: list[MountEntry] = []
        self.procs: list[list[str]] = []
        self.points: dict[str, str] = {DEVICE.device: mount_point}
        self.pid = 4242
        self.slept: list[float] = []
        self.write_probe_called = False
        self.ntfs3g_calls = 0
        self.evictions = 0
        self.mounts_broken = False
        self._ntfs3g_rc = 0
        self._ntfs3g_mounts = True
        self._stderr = ""
        self._apple_returns = False
        self._apple_returned = False
        self._device_present = True
        self._probe_error: Exception | None = None
        self._diskutil_unmount_rc = 0
        self._mount_reads = 0
        self._mounts_error_from: int | None = None
        self._interrupt_at: int | None = None
        self._break_mounts_on_probe = False

    # --- injected callables -------------------------------------------------
    def run(self, argv):
        self.calls.append(list(argv))
        if argv[0] == "test":
            return (0 if self._device_present else 1, "")
        if argv[0] == NTFS3G:
            self.ntfs3g_calls += 1
            if self._ntfs3g_rc == 0 and self._ntfs3g_mounts:
                self.table.append(
                    MountEntry(
                        source=f"fuse-t:/{volname(argv)}",
                        mount_point=argv[2],
                        fs="nfs",
                        read_only=False,
                    )
                )
                self.procs.append(list(argv))
            return (self._ntfs3g_rc, self._stderr)
        if argv[:2] == ["diskutil", "unmount"]:
            if self.ntfs3g_calls and argv[2] != self.mount_point:
                self.evictions += 1
            if self._diskutil_unmount_rc != 0:
                return (self._diskutil_unmount_rc, "Resource busy")
            self._remove(argv[2])
            return (0, "")
        if argv[0] == "umount":
            self._remove(argv[-1])
            return (0, "")
        if argv[:2] == ["diskutil", "mount"]:
            point = self.points.get(argv[2], self.mount_point)
            self.table.append(
                MountEntry(source=argv[2], mount_point=point, fs="ntfs", read_only=True)
            )
            return (0, "")
        if argv[0] == "pgrep":
            pattern = argv[2]
            if any(re.search(pattern, " ".join(proc)) for proc in self.procs):
                return (0, f"{self.pid}\n")
            return (1, "")
        raise AssertionError(f"unexpected command: {argv}")

    def mounts(self):
        self._mount_reads += 1
        if self._interrupt_at is not None and self._mount_reads == self._interrupt_at:
            # One interrupted read, like a Ctrl-C that lands mid-command.
            self._interrupt_at = None
            raise KeyboardInterrupt
        if self.mounts_broken:
            raise CalledProcessError(1, ["mount"])
        error_from = self._mounts_error_from
        if error_from is not None and self._mount_reads >= error_from:
            raise CalledProcessError(1, ["mount"])
        if self.ntfs3g_calls and self._apple_returns and not self._apple_returned:
            self._apple_returned = True
            self.table.append(APPLE_REMOUNT)
        return list(self.table)

    def sleep(self, seconds):
        self.slept.append(seconds)

    def probe(self, mount_point):
        assert mount_point in (MOUNT_POINT, OTHER_MOUNT_POINT)
        self.write_probe_called = True
        if self._probe_error is not None:
            if self._break_mounts_on_probe:
                self.mounts_broken = True
            raise self._probe_error

    # --- scripting ----------------------------------------------------------
    def seed(self, state, device=DEVICE, mount_point=None):
        """Put the host in ``state`` for one device, served by a real process."""
        point = mount_point or self.points.setdefault(device.device, self.mount_point)
        self.points[device.device] = point
        if state == "ro":
            self.table = [ro_entry(device, point)]
        elif state == "rw":
            self.table = [rw_entry(device, point)]
            self.serve(device, point)
        else:
            self.table = []
        return point

    def serve(self, device, mount_point):
        """Record the ntfs-3g process and mount entry a real `ntfs-3g` leaves."""
        if not any(
            proc[1] == device.device and proc[2] == mount_point for proc in self.procs
        ):
            self.procs.append(
                [NTFS3G, device.device, mount_point, "-o", f"volname={device.label}"]
            )
        if not any(e.mount_point == mount_point for e in self.table):
            self.table.append(rw_entry(device, mount_point))

    def configure(
        self,
        *,
        ntfs3g_rc=0,
        ntfs3g_mounts=True,
        stderr="",
        apple_returns=False,
        device_present=True,
        probe_error=None,
        diskutil_unmount_rc=0,
        mounts_error_from=None,
        interrupt_at=None,
        break_mounts_on_probe=False,
    ):
        self._ntfs3g_rc = ntfs3g_rc
        self._ntfs3g_mounts = ntfs3g_mounts
        self._stderr = stderr
        self._apple_returns = apple_returns
        self._device_present = device_present
        self._probe_error = probe_error
        self._diskutil_unmount_rc = diskutil_unmount_rc
        self._mounts_error_from = mounts_error_from
        self._interrupt_at = interrupt_at
        self._break_mounts_on_probe = break_mounts_on_probe

    def mount(
        self,
        *,
        state="unmounted",
        device=DEVICE,
        mount_point=None,
        seed=True,
        recover=False,
        remove_hiberfile=False,
        timeout=30,
        label_shared=False,
        **configure_options,
    ):
        point = mount_point or self.points.get(device.device, self.mount_point)
        if seed:
            self.procs = []
            self.seed(state, device, point)
        self.configure(**configure_options)
        return self.invoke(
            device,
            point,
            recover=recover,
            remove_hiberfile=remove_hiberfile,
            timeout=timeout,
            label_shared=label_shared,
        )

    def invoke(self, device=DEVICE, mount_point=None, *, recover=False,
               remove_hiberfile=False, timeout=30, label_shared=False):
        point = mount_point or self.points.get(device.device, self.mount_point)
        return mount_volume(
            Volume(
                device=device,
                mounts=tuple(self.volume_mounts(device, point)),
            ),
            uid=501,
            gid=20,
            run=self.run,
            mounts=self.mounts,
            sleep=self.sleep,
            timeout=timeout,
            probe=self.probe,
            recover=recover,
            remove_hiberfile=remove_hiberfile,
            label_shared=label_shared,
        )

    # --- helpers ------------------------------------------------------------
    @property
    def mount_point(self):
        return self.points[DEVICE.device]

    def volume_mounts(self, device, point):
        """What `build_volumes` would attribute to `device` in the live table."""
        return [
            entry
            for entry in self.table
            if entry.source == device.device
            or (bool(device.label) and entry.source == f"fuse-t:/{device.label}")
            or entry.mount_point == point
        ]

    def _remove(self, mount_point):
        self.table = [e for e in self.table if e.mount_point != mount_point]
        self.procs = [p for p in self.procs if p[2] != mount_point]

    def ntfs3g_invocations(self):
        return sum(1 for call in self.calls if call[0] == NTFS3G)

    def pgrep_calls(self):
        return [call for call in self.calls if call[0] == "pgrep"]

    def state(self, label):
        if any(e.fs == "nfs" and e.source == f"fuse-t:/{label}" for e in self.table):
            return "rw"
        return "ro" if self.table else "unmounted"


def volname(argv):
    options = argv[argv.index("-o") + 1]
    return next(
        part.split("=", 1)[1]
        for part in options.split(",")
        if part.startswith("volname=")
    )


class Owned:
    """Stand-in for `os.stat_result`'s ownership fields."""

    def __init__(self, uid=501, gid=20):
        self.st_uid = uid
        self.st_gid = gid


@pytest.fixture(autouse=True)
def mount_point_dirs(monkeypatch):
    """Keep host writes out of the tests: no mkdir, no real probe, no real stat."""
    created: list[str] = []
    monkeypatch.setattr(mounter, "_ensure_mount_point", created.append)

    def unexpected_real_probe(mount_point):
        # `pytest.fail` derives from BaseException, so it escapes the mounter's
        # `except Exception` guard and fails the test loudly.
        pytest.fail(f"test used the real write probe on {mount_point}")

    # `mount_volume` resolves these through module attributes; the tests that
    # exercise the real probe call the directly imported function instead.
    monkeypatch.setattr(mounter, "_write_probe", unexpected_real_probe)
    monkeypatch.setattr(mounter, "_default_stat", lambda path: Owned())
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


def test_same_label_live_mount_at_the_target_is_refused_not_evicted(
    fake_env, mount_point_dirs
):
    # Drive A (disk4s1, label DATA) is read-write at /Volumes/DATA. Drive B
    # (disk6s1, same label) must not evict A to make room for itself.
    live = MountEntry("fuse-t:/DATA", OTHER_MOUNT_POINT, "nfs", False)
    fake_env.points[DATA_DISK.device] = OTHER_MOUNT_POINT
    fake_env.points[OTHER.device] = OTHER_MOUNT_POINT
    fake_env.table = [live]
    fake_env.procs = [[NTFS3G, DATA_DISK.device, OTHER_MOUNT_POINT, "-o", "volname=DATA"]]
    fake_env.configure()

    with pytest.raises(MountFailed) as err:
        fake_env.invoke(OTHER, OTHER_MOUNT_POINT, label_shared=True)

    message = str(err.value)
    assert OTHER_MOUNT_POINT in message
    assert "'DATA'" in message
    assert OTHER.device in message
    assert "unmount it before mounting this one" in message
    assert mount_point_dirs == []
    assert not [c for c in fake_env.calls if c[:2] == ["diskutil", "unmount"]]
    assert fake_env.ntfs3g_invocations() == 0
    assert fake_env.table == [live]


def test_same_label_mount_served_by_this_device_is_idempotent(fake_env):
    fake_env.points[DATA_DISK.device] = OTHER_MOUNT_POINT
    fake_env.table = [MountEntry("fuse-t:/DATA", OTHER_MOUNT_POINT, "nfs", False)]
    fake_env.procs = [[NTFS3G, DATA_DISK.device, OTHER_MOUNT_POINT, "-o", "volname=DATA"]]
    fake_env.configure()

    result = fake_env.invoke(DATA_DISK, OTHER_MOUNT_POINT, label_shared=True)

    assert result.mount_point == OTHER_MOUNT_POINT
    assert result.pid is None
    assert fake_env.ntfs3g_invocations() == 0
    assert not [c for c in fake_env.calls if c[0] == "diskutil"]


def test_mount_is_not_satisfied_by_another_labels_mount(fake_env):
    # HD1 is unmounted; only HD2 (a different label) is read-write. HD2's entry
    # says nothing about HD1, so HD1 must actually be mounted.
    fake_env.points[HD2.device] = HD2_MOUNT_POINT
    fake_env.table = [rw_entry(HD2, HD2_MOUNT_POINT)]
    fake_env.procs = [[NTFS3G, HD2.device, HD2_MOUNT_POINT, "-o", "volname=HD2"]]
    fake_env.configure()
    drive_hd1 = next(
        v
        for v in build_volumes([DEVICE, HD2], fake_env.mounts())
        if v.device is DEVICE
    )
    assert drive_hd1.state == "unmounted"

    result = mount_volume(
        drive_hd1,
        uid=501,
        gid=20,
        run=fake_env.run,
        mounts=fake_env.mounts,
        sleep=fake_env.sleep,
        probe=fake_env.probe,
    )

    ntfs3g = next(call for call in fake_env.calls if call[0] == NTFS3G)
    assert ntfs3g[1] == DEVICE.device
    assert ntfs3g[2] == MOUNT_POINT
    assert fake_env.ntfs3g_invocations() == 1
    assert result.mount_point == MOUNT_POINT
    assert rw_entry(HD2, HD2_MOUNT_POINT) in fake_env.table
    assert ["diskutil", "unmount", HD2_MOUNT_POINT] not in fake_env.calls
    assert [
        "pgrep",
        "-f",
        "ntfs-3g .*/dev/disk6s1( |$)",
    ] not in fake_env.calls


def test_mount_is_idempotent_for_its_own_label_while_another_label_is_mounted(
    fake_env,
):
    fake_env.points[HD2.device] = HD2_MOUNT_POINT
    fake_env.table = [rw_entry(DEVICE, MOUNT_POINT), rw_entry(HD2, HD2_MOUNT_POINT)]
    fake_env.procs = [
        [NTFS3G, DEVICE.device, MOUNT_POINT, "-o", "volname=HD1"],
        [NTFS3G, HD2.device, HD2_MOUNT_POINT, "-o", "volname=HD2"],
    ]
    fake_env.configure()
    drive_hd1 = next(
        v
        for v in build_volumes([DEVICE, HD2], fake_env.mounts())
        if v.device is DEVICE
    )
    assert drive_hd1.state == "rw"

    result = mount_volume(
        drive_hd1,
        uid=501,
        gid=20,
        run=fake_env.run,
        mounts=fake_env.mounts,
        sleep=fake_env.sleep,
        probe=fake_env.probe,
    )

    assert result.mount_point == MOUNT_POINT
    assert result.pid is None
    assert not [call for call in fake_env.calls if call[0] in (NTFS3G, "diskutil")]
    assert fake_env.table == [
        rw_entry(DEVICE, MOUNT_POINT),
        rw_entry(HD2, HD2_MOUNT_POINT),
    ]


def test_a_live_mount_elsewhere_does_not_count_as_idempotent(fake_env):
    # Our label is live at /Volumes/HD1-old, not at the requested mount point.
    # Success there would claim a mount this path does not have. The snapshot
    # is what discovery returned before the mount moved; the table read during
    # the run is the live one.
    stale = MountEntry("fuse-t:/HD1", "/Volumes/HD1-old", "nfs", False)
    fake_env.table = [stale]
    fake_env.procs = [[NTFS3G, DEVICE.device, "/Volumes/HD1-old", "-o", "volname=HD1"]]
    fake_env.configure()
    discovered = next(
        v for v in build_volumes([DEVICE], [OURS]) if v.device is DEVICE
    )
    assert discovered.state == "rw"
    assert discovered.mount_point == MOUNT_POINT

    result = mount_volume(
        discovered,  # snapshot: read-write at MOUNT_POINT
        uid=501,
        gid=20,
        run=fake_env.run,
        mounts=fake_env.mounts,
        sleep=fake_env.sleep,
        probe=fake_env.probe,
    )

    ntfs3g = next(call for call in fake_env.calls if call[0] == NTFS3G)
    assert ntfs3g[2] == MOUNT_POINT
    assert result.mount_point == MOUNT_POINT
    assert ["diskutil", "unmount", "/Volumes/HD1-old"] not in fake_env.calls


def test_restore_does_not_unmount_what_is_not_a_fuse_t_mount(fake_env):
    # No FUSE-T entry sits at the mount point when the failure happens, so the
    # restore goes straight to Apple's mount instead of evicting anything.
    with pytest.raises(MountFailed):
        fake_env.mount(state="unmounted", device_present=False)

    assert not [call for call in fake_env.calls if call[:2] == ["diskutil", "unmount"]]
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_restore_leaves_an_apple_mount_at_the_mount_point_alone(fake_env):
    # Apple remounted read-only at the target before the mount failed: the
    # restore must not unmount Apple's own mount, only claim it back.
    remounted = f"{MOUNT_POINT} 1"
    apple_remount = ro_entry(DEVICE, remounted)

    with pytest.raises(MountFailed) as err:
        fake_env.mount(
            state="unmounted",
            mount_point=remounted,
            ntfs3g_mounts=False,
            apple_returns=True,
            timeout=1,
        )

    assert "read-only mount was restored" in str(err.value).lower()
    assert apple_remount in fake_env.table
    assert not [call for call in fake_env.calls if call[:2] == ["diskutil", "unmount"]]
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_a_foreign_labels_mount_at_the_target_is_refused_not_evicted(
    fake_env, mount_point_dirs
):
    # The mount at HD1's target names HD2. It is not HD1's mount, and it is not
    # a same-label sibling either: refuse, name it honestly, touch nothing.
    foreign = MountEntry("fuse-t:/HD2", MOUNT_POINT, "nfs", False)
    fake_env.table = [foreign]
    fake_env.procs = [[NTFS3G, HD2.device, MOUNT_POINT, "-o", "volname=HD2"]]
    fake_env.configure()
    drive_hd1 = next(
        v
        for v in build_volumes([DEVICE, HD2], fake_env.mounts())
        if v.device is DEVICE
    )

    with pytest.raises(MountFailed) as err:
        mount_volume(
            drive_hd1,
            uid=501,
            gid=20,
            run=fake_env.run,
            mounts=fake_env.mounts,
            sleep=fake_env.sleep,
            probe=fake_env.probe,
        )

    message = str(err.value)
    assert MOUNT_POINT in message
    assert "'HD2'" in message
    assert "shares the label" not in message
    assert "unmount it before mounting this one" in message
    assert mount_point_dirs == []
    assert not [call for call in fake_env.calls if call[:2] == ["diskutil", "unmount"]]
    assert fake_env.ntfs3g_invocations() == 0
    assert fake_env.table == [foreign]


def test_pgrep_uses_the_precise_device_pattern(fake_env):
    fake_env.mount(state="rw")

    assert fake_env.pgrep_calls() == [
        ["pgrep", "-f", "ntfs-3g .*/dev/disk4s1( |$)"]
    ]


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


def test_settles_after_eviction_before_mounting(fake_env):
    fake_env.mount(state="ro")

    assert fake_env.slept == [mounter.SETTLE_SECONDS]
    assert fake_env.ntfs3g_invocations() == 1


def test_evicts_apple_remount_that_lands_during_settle(fake_env):
    point = fake_env.points[DEVICE.device]
    record = fake_env.sleep
    remounts = []

    def remount(seconds):
        record(seconds)
        if not remounts:
            remounts.append(True)
            fake_env.table.append(ro_entry(DEVICE, point))

    fake_env.sleep = remount
    fake_env.mount(state="ro")

    unmounts = [c for c in fake_env.calls if c[:2] == ["diskutil", "unmount"]]
    assert len(unmounts) == 2
    assert fake_env.slept == [mounter.SETTLE_SECONDS, mounter.SETTLE_SECONDS]
    assert fake_env.ntfs3g_invocations() == 1


def test_gives_up_when_apple_keeps_remounting(fake_env):
    point = fake_env.points[DEVICE.device]
    record = fake_env.sleep

    def remount(seconds):
        record(seconds)
        fake_env.table.append(ro_entry(DEVICE, point))

    fake_env.sleep = remount
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro")

    assert "keeps remounting" in str(err.value)
    assert "read-only mount was restored" in str(err.value)
    assert fake_env.ntfs3g_invocations() == 0


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
    fake_env.table = [APPLE_MOUNT]

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


def test_recreates_the_mount_point_after_apple_unmount(fake_env, monkeypatch, tmp_path):
    """FSKit deletes the mount-point directory when it unmounts the volume."""
    point = tmp_path / "HD1"
    point.mkdir()
    monkeypatch.setattr(
        mounter,
        "_ensure_mount_point",
        lambda path: Path(path).mkdir(parents=True, exist_ok=True),
    )

    original_run = fake_env.run
    unmounted = []

    def run(argv):
        if argv[:2] == ["diskutil", "unmount"] and point.exists():
            point.rmdir()  # FSKit removes the directory too, not just the mount
            unmounted.append(argv[2])
        if argv[0] == NTFS3G:
            assert point.is_dir(), "ntfs-3g started with the mount point deleted"
        return original_run(argv)

    fake_env.run = run
    fake_env.probe = lambda mount_point: None

    result = fake_env.mount(state="ro", mount_point=str(point))

    assert unmounted, "the Apple unmount never ran, so the race was not exercised"
    assert result.mount_point == str(point)
    assert fake_env.state("HD1") == "rw"


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
    assert "write check failed" in message
    assert [c for c in fake_env.calls if c == ["diskutil", "unmount", MOUNT_POINT]]
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_verified_restore_is_reported_as_restored(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro", probe_error=OSError(30, "Read-only file system"))

    message = str(err.value).lower()
    assert "read-only mount was restored" in message
    assert "attempted" not in message


def test_interrupt_after_eviction_still_restores_read_only(fake_env):
    # Apple's mount has been evicted when the mount-table read is interrupted.
    # Reads: initial, eviction pass, settle re-check, then the wait-for-mount
    # poll that is interrupted here.
    with pytest.raises(KeyboardInterrupt):
        fake_env.mount(state="ro", interrupt_at=4)

    assert fake_env.ntfs3g_invocations() == 1
    assert ["diskutil", "unmount", MOUNT_POINT] in fake_env.calls
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


def test_unreadable_table_in_the_failure_tail_unmounts_then_attempts_restore(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(
            state="ro",
            probe_error=OSError(30, "Read-only file system"),
            break_mounts_on_probe=True,
        )

    message = str(err.value).lower()
    assert ["diskutil", "unmount", MOUNT_POINT] in fake_env.calls
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert "read-only restore attempted" in message
    assert "was restored" not in message


def test_successful_ntfs3g_without_a_mount_names_the_timeout(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro", ntfs3g_mounts=False, timeout=2)

    message = str(err.value)
    assert "did not appear within 2 seconds" in message
    assert "exit code 0" not in message


def test_mount_owned_by_another_user_fails_and_restores(fake_env, monkeypatch):
    # The write probe ran as root; the invoking user must still own the mount.
    monkeypatch.setattr(mounter, "_default_stat", lambda path: Owned(uid=0, gid=0))

    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro")

    message = str(err.value)
    assert fake_env.write_probe_called
    assert "owned by uid 0" in message
    assert "expected uid 501" in message
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_unlabeled_volume_is_refused_before_touching_the_stack(fake_env):
    unlabeled = Device(
        identifier="disk7s1",
        device="/dev/disk7s1",
        label="",
        uuid="UUID-UNLABELED",
        size=1,
        fs_type="Windows_NTFS",
        mount_point=None,
    )

    with pytest.raises(MountFailed) as err:
        fake_env.mount(device=unlabeled, state="ro")

    message = str(err.value)
    assert "no label" in message
    assert "relabel" in message
    assert fake_env.calls == []


def test_unexpected_probe_error_restores_read_only(fake_env):
    with pytest.raises(MountFailed) as err:
        fake_env.mount(state="ro", probe_error=RuntimeError("probe blew up"))

    message = str(err.value)
    assert "write check failed" in message
    assert "probe blew up" in message
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_restore_falls_back_to_umount_before_replacing_apple_mount(fake_env):
    with pytest.raises(MountFailed):
        fake_env.mount(
            state="ro", probe_error=OSError(5, "I/O error"), diskutil_unmount_rc=1
        )

    ntfs3g_index = next(i for i, c in enumerate(fake_env.calls) if c[0] == NTFS3G)
    post_mount_umounts = [
        i
        for i, c in enumerate(fake_env.calls)
        if i > ntfs3g_index and c[:2] == ["umount", "-f"]
    ]
    assert post_mount_umounts, "restore path must fall back to umount -f"
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]
    assert fake_env.state("HD1") == "ro"


def test_mount_table_failure_still_restores_read_only(fake_env):
    # `read_mount_table` runs `mount`, which can itself fail. From the third read
    # on (the post-mount poll) the table is broken, so verification cannot pass.
    with pytest.raises(MountFailed):
        fake_env.mount(state="ro", mounts_error_from=3)

    assert fake_env.ntfs3g_invocations() == 1
    assert fake_env.calls[-1] == ["diskutil", "mount", "/dev/disk4s1"]


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


@pytest.mark.parametrize(
    ("flags", "present", "absent"),
    [
        ({}, [], ["recover", "remove_hiberfile"]),
        ({"recover": True}, ["recover"], ["remove_hiberfile"]),
        ({"remove_hiberfile": True}, ["remove_hiberfile"], ["recover"]),
    ],
)
def test_destructive_options_are_opt_in(fake_env, flags, present, absent):
    fake_env.mount(state="ro", **flags)

    argv = next(call for call in fake_env.calls if call[0] == NTFS3G)
    options = argv[argv.index("-o") + 1].split(",")
    for token in present:
        assert token in options
    for token in absent:
        assert token not in options


def test_real_probe_writes_reads_back_and_cleans_up(tmp_path):
    _write_probe(str(tmp_path))

    assert not (tmp_path / ".ntfsmnt-probe").exists()


def test_real_probe_raises_oserror_when_it_cannot_write(tmp_path):
    with pytest.raises(OSError):
        _write_probe(str(tmp_path / "missing"))
