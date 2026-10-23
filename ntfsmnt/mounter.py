"""Mount orchestration: turn Apple's read-only NTFS mount into a read-write one.

macOS mounts NTFS read-only through FSKit and can remount read-only at any
moment, so getting a writable volume means evicting Apple's mount, mounting
through ntfs-3g on FUSE-T, proving the result is writable, and restoring Apple's
mount if anything fails.

Every side effect goes through the injected ``run``, ``mounts``, ``sleep`` and
``probe`` callables, so the whole sequence runs without touching the host.
"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

from ntfsmnt.identity import (
    Ownership,
    is_fuse_t,
    names_label,
    owner,
    parse_pid,
    pid_pattern,
    source_for,
)
from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.options import build_mount_args, classify_mount_failure
from ntfsmnt.volumes import Volume

PROBE_FILENAME = ".ntfsmnt-probe"
DEVICE_WAIT_ATTEMPTS = 10
#: How long each quiet window is before ntfs-3g starts. FSKit can put Apple's
#: read-only mount back after the first eviction, so the mounter re-checks for a
#: remount instead of trusting a single unmount. (The exit-66 failures were a
#: different bug: FSKit deletes the mount-point directory, recreated in step 4c.)
SETTLE_SECONDS = 8
#: How many clean settle windows to try when macOS keeps remounting read-only.
SETTLE_ATTEMPTS = 3


@dataclass(frozen=True)
class MountResult:
    volume: Volume
    mount_point: str
    pid: int | None


class MountFailed(Exception):
    """The volume could not be mounted read-write. The message is user-facing."""


def _ownership(
    entries: list[MountEntry],
    device: Device,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    label_shared: bool,
) -> list[tuple[MountEntry, Ownership]]:
    """Pair this volume's candidate entries with what they say about it.

    Only entries whose source is ``fuse-t:/<label>`` are candidates: an entry
    for another label is another volume's mount, and no ``pgrep`` answer may
    turn it into this volume's.

    Every ``pgrep`` happens here, once, before anything is unmounted: the
    answers decide idempotency and whether a FUSE-T mount blocks the target.
    """
    return [
        (
            entry,
            owner(
                entry,
                device=device.device,
                label=device.label,
                run=run,
                label_shared=label_shared,
            ),
        )
        for entry in entries
        if names_label(entry, device.label)
    ]


def _has_our_mount(entries: list[MountEntry], mount_point: str, label: str) -> bool:
    return any(names_label(e, label) and e.mount_point == mount_point for e in entries)


def _safe_mounts(mounts: Callable[[], list[MountEntry]]) -> list[MountEntry]:
    """Read the mount table without letting a broken `mount` escape a failure path."""
    try:
        return mounts()
    except Exception:
        return []


def _ensure_mount_point(mount_point: str) -> None:
    Path(mount_point).mkdir(parents=True, exist_ok=True)


def _default_stat(path: str) -> os.stat_result:
    """The real `os.stat`, resolved at call time so tests can replace it."""
    return os.stat(path)


def _write_probe(mount_point: str) -> None:
    """Write, read back, and remove a file to prove the mount is writable."""
    path = Path(mount_point) / PROBE_FILENAME
    path.write_text("ok", encoding="utf-8")
    try:
        if path.read_text(encoding="utf-8") != "ok":
            raise OSError(f"wrote {path} but could not read it back")
    finally:
        path.unlink(missing_ok=True)


def _unmount(mount_point: str, *, run: Callable[[list[str]], tuple[int, str]]) -> None:
    returncode, _ = run(["diskutil", "unmount", mount_point])
    if returncode != 0:
        run(["umount", "-f", mount_point])


def _wait_for_device(
    device_path: str,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    sleep: Callable[[float], None],
) -> bool:
    for attempt in range(DEVICE_WAIT_ATTEMPTS):
        returncode, _ = run(["test", "-e", device_path])
        if returncode == 0:
            return True
        if attempt + 1 < DEVICE_WAIT_ATTEMPTS:
            sleep(1)
    return False


def _wait_for_mount(
    mounts: Callable[[], list[MountEntry]],
    mount_point: str,
    *,
    label: str,
    timeout: int,
    sleep: Callable[[float], None],
) -> bool:
    for _ in range(max(1, timeout)):
        if _has_our_mount(_safe_mounts(mounts), mount_point, label):
            return True
        sleep(1)
    return _has_our_mount(_safe_mounts(mounts), mount_point, label)


def _apple_entries(
    entries: list[MountEntry], device_path: str, mount_point: str
) -> list[MountEntry]:
    """Apple's mounts of this device, or read-only mounts at this mount point."""
    return [
        entry
        for entry in entries
        if not is_fuse_t(entry)
        and (
            entry.source == device_path
            or (entry.mount_point == mount_point and entry.source.startswith("/dev/"))
        )
    ]


def _evict_apple_entries(
    entries: list[MountEntry],
    device_path: str,
    mount_point: str,
    *,
    run: Callable[[list[str]], tuple[int, str]],
) -> None:
    """Unmount Apple's mounts of this device, or at this device's mount point."""
    for entry in _apple_entries(entries, device_path, mount_point):
        _unmount(entry.mount_point, run=run)


def _evict_apple_remounts(
    device_path: str,
    mount_point: str,
    *,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
) -> None:
    for entry in _safe_mounts(mounts):
        if entry.source == device_path and entry.mount_point != mount_point:
            _unmount(entry.mount_point, run=run)


def _settled_clear(
    device_path: str,
    mount_point: str,
    *,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
    sleep: Callable[[float], None],
) -> bool:
    """Wait for a clean settle window, evicting a read-only remount if it lands.

    FSKit can put Apple's read-only mount back after the first eviction, so
    ntfs-3g only starts after ``SETTLE_SECONDS`` with nothing mounted at the
    device or the path. Returns whether such a window was found.
    """
    for _ in range(SETTLE_ATTEMPTS):
        sleep(SETTLE_SECONDS)
        entries = _apple_entries(_safe_mounts(mounts), device_path, mount_point)
        if not entries:
            return True
        for entry in entries:
            _unmount(entry.mount_point, run=run)
    return False


def _restore_read_only(
    *,
    device_path: str,
    mount_point: str,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
) -> str:
    """Tear down our mount, then put Apple's read-only mount back, last of all.

    The teardown only touches a genuine FUSE-T mount at ``mount_point``: a fresh
    best-effort table read decides, so an Apple mount, an empty path, or a
    foreign table never causes an eviction of someone else's FUSE-T mount. When
    the table cannot be read the unmount is still attempted — a mount left live
    by a guess would keep the restore from happening at all. Its return code is
    ignored: on a failure path the only thing that matters is that nothing of
    ours keeps the mount point busy. Apple's mount is restored by device, which
    is device-identified.

    Returns the wording for a message about the outcome. Restoration is only
    claimed when the mount table can be re-read and shows the device mounted;
    otherwise the honest claim is that the restore was attempted.
    """
    try:
        entries = mounts()
    except Exception:
        entries = None
    if entries is None or any(
        is_fuse_t(entry) and entry.mount_point == mount_point for entry in entries
    ):
        _unmount(mount_point, run=run)
    run(["diskutil", "mount", device_path])
    try:
        entries = mounts()
    except Exception:
        return "Read-only restore attempted."
    if any(entry.source == device_path for entry in entries):
        return "Apple's read-only mount was restored."
    return "Read-only restore attempted."


def _fail(
    message: str,
    *,
    device_path: str,
    mount_point: str,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
) -> NoReturn:
    wording = _restore_read_only(
        device_path=device_path, mount_point=mount_point, mounts=mounts, run=run
    )
    raise MountFailed(f"{message} {wording}")


def mount_volume(
    volume: Volume,
    *,
    uid: int,
    gid: int,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    sleep: Callable[[float], None],
    timeout: int = 30,
    recover: bool = False,
    remove_hiberfile: bool = False,
    label_shared: bool = False,
    probe: Callable[[str], None] | None = None,
    stat: Callable[[str], os.stat_result] | None = None,
) -> MountResult:
    device = volume.device
    if device.label == "":
        # Every path from here assumes a label: it names the FUSE-T source and
        # the default mount point. Refuse before evicting anyone's mount.
        raise MountFailed(
            f"{device.device}: volume has no label; relabel it before mounting"
        )
    mount_point = volume.mount_point or f"/Volumes/{device.label}"
    entries = mounts()
    verdicts = _ownership(entries, device, run=run, label_shared=label_shared)

    # 1. Already served by us at the target: succeed without touching the stack.
    # An entry that resolves to this volume somewhere else is a stale mount, not
    # a success for this path. With a unique label the entry is this volume's
    # even when pgrep cannot answer; with a shared label only a mount whose
    # process names *this* device counts, and an unanswerable pgrep is a
    # refusal, not a success.
    for entry, verdict in verdicts:
        if entry.mount_point != mount_point:
            continue
        if verdict == "ours" or (verdict == "unknown" and not label_shared):
            return MountResult(volume=volume, mount_point=mount_point, pid=None)

    # 2. A FUSE-T mount at the target belongs to a volume, never to this one:
    # refuse before creating the mount point or evicting anything, so a
    # same-label drive keeps its mount.
    occupied = next(
        (e for e in entries if is_fuse_t(e) and e.mount_point == mount_point), None
    )
    if occupied is not None:
        if occupied.source == source_for(device.label):
            holder = (
                f"its volume shares the label '{device.label}' with {device.device}"
            )
        else:
            holder = (
                f"it belongs to {occupied.source.removeprefix('fuse-t:/')!r}, "
                f"not to {device.label!r}"
            )
        raise MountFailed(
            f"mount point {mount_point} is already a read-write mount; "
            f"{holder} — unmount it before mounting this one"
        )

    # 3. Evict Apple's read-only mount of this device, or Apple's mount at the
    # mount point. From the first eviction on, every exit must restore Apple's
    # mount — including an interrupt, so the sequence is guarded by
    # BaseException below.
    try:
        _evict_apple_entries(mounts(), device.device, mount_point, run=run)

        # 4. Wait for the device node to settle after the unmount.
        if not _wait_for_device(device.device, run=run, sleep=sleep):
            _fail(
                f"The device {device.device} for {device.label} did not reappear. "
                "Is the disk still connected?",
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 4b. FSKit can remount Apple's volume while its teardown is in flight,
        # so only start after a settle window with nothing mounted at the
        # device or the path.
        if not _settled_clear(
            device.device,
            mount_point,
            mounts=mounts,
            run=run,
            sleep=sleep,
        ):
            _fail(
                f"{device.label} is still mounted read-only at {mount_point} "
                "and macOS keeps remounting it, so the read-write mount could "
                "not start.",
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 4c. Unmounting Apple's FSKit volume also deletes the mount-point
        # directory, so the path is gone by the time we get here. Recreate it
        # immediately before ntfs-3g starts: FUSE-T's NFS mount helper fails
        # with mount_nfs exit 66 when the mount point vanishes underneath it.
        try:
            _ensure_mount_point(mount_point)
        except OSError as exc:
            _fail(
                f"Could not create mount point {mount_point}: {exc.strerror or exc}",
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 5. Mount read-write through ntfs-3g.
        returncode, stderr = run(
            build_mount_args(
                device.device,
                mount_point,
                uid=uid,
                gid=gid,
                label=device.label,
                recover=recover,
                remove_hiberfile=remove_hiberfile,
            )
        )

        # 6. Confirm the FUSE-T mount at the mount point actually appeared.
        if returncode != 0 or not _wait_for_mount(
            mounts, mount_point, label=device.label, timeout=timeout, sleep=sleep
        ):
            _fail(
                classify_mount_failure(
                    returncode=returncode, stderr=stderr, timeout=timeout
                ),
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 7. Apple can remount read-only at any moment; evict its competition.
        _evict_apple_remounts(device.device, mount_point, mounts=mounts, run=run)

        # 8. Prove the mount is writable, not merely present.
        try:
            (probe or _write_probe)(mount_point)
        except Exception as exc:
            _fail(
                f"{device.label} mounted at {mount_point} but the write check "
                f"failed (the volume is not writable): {exc}.",
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 9. Prove the *invoking user* owns the mount. Step 8 ran as root, so a
        # successful write there says nothing about an unprivileged write.
        mount_stat = (stat or _default_stat)(mount_point)
        if (mount_stat.st_uid, mount_stat.st_gid) != (uid, gid):
            _fail(
                f"{device.label} mounted at {mount_point} but owned by "
                f"uid {mount_stat.st_uid}, gid {mount_stat.st_gid}, "
                f"expected uid {uid}, gid {gid}.",
                device_path=device.device,
                mount_point=mount_point,
                mounts=mounts,
                run=run,
            )

        # 10. Report the ntfs-3g process serving the mount, when we can see it.
        pid = parse_pid(run(["pgrep", "-f", pid_pattern(device.device)])[1])
        return MountResult(volume=volume, mount_point=mount_point, pid=pid)
    except MountFailed:
        # _fail already restored the read-only mount before raising.
        raise
    except BaseException:
        # Unexpected failure or interrupt (e.g. the mount table cannot be read,
        # or the user pressed Ctrl-C): put Apple's read-only mount back before
        # letting the exception escape. `except MountFailed` above keeps the
        # message path.
        _restore_read_only(
            device_path=device.device,
            mount_point=mount_point,
            mounts=mounts,
            run=run,
        )
        raise
