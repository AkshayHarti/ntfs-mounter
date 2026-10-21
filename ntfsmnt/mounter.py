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

from ntfsmnt.identity import Ownership, is_fuse_t, owner, parse_pid, pid_pattern
from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.options import build_mount_args, classify_mount_failure
from ntfsmnt.volumes import Volume

PROBE_FILENAME = ".ntfsmnt-probe"
DEVICE_WAIT_ATTEMPTS = 10


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
    """Pair every entry with what it says about this volume.

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
    ]


def _has_our_mount(entries: list[MountEntry], mount_point: str) -> bool:
    return any(is_fuse_t(e) and e.mount_point == mount_point for e in entries)


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
    timeout: int,
    sleep: Callable[[float], None],
) -> bool:
    for _ in range(max(1, timeout)):
        if _has_our_mount(_safe_mounts(mounts), mount_point):
            return True
        sleep(1)
    return _has_our_mount(_safe_mounts(mounts), mount_point)


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


def _restore_read_only(
    *,
    device_path: str,
    mount_point: str,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
) -> str:
    """Tear down our mount, then put Apple's read-only mount back, last of all.

    The teardown is unconditional and its return code ignored: on a failure path
    the only thing that matters is that nothing of ours keeps the mount point
    busy. Apple's mount is restored by device, which is device-identified.

    Returns the wording for a message about the outcome. Restoration is only
    claimed when the mount table can be re-read and shows the device mounted;
    otherwise the honest claim is that the restore was attempted.
    """
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
    verdicts = _ownership(mounts(), device, run=run, label_shared=label_shared)

    # 1. Already served by us: succeed without touching the stack. With a
    # unique label the entry is this volume's even when pgrep cannot answer;
    # with a shared label only a mount whose process names *this* device
    # counts, and an unanswerable pgrep is a refusal, not a success.
    for _entry, verdict in verdicts:
        if verdict == "ours" or (verdict == "unknown" and not label_shared):
            return MountResult(volume=volume, mount_point=mount_point, pid=None)

    # 2. A FUSE-T mount at the target belongs to a volume, never to this one:
    # refuse before creating the mount point or evicting anything, so a
    # same-label drive keeps its mount.
    if any(is_fuse_t(e) and e.mount_point == mount_point for e, _ in verdicts):
        raise MountFailed(
            f"mount point {mount_point} is already a read-write mount; its "
            f"volume shares the label '{device.label}' with {device.device} — "
            f"unmount it before mounting this one"
        )

    try:
        _ensure_mount_point(mount_point)
    except OSError as exc:
        raise MountFailed(
            f"Could not create mount point {mount_point}: {exc.strerror or exc}"
        ) from exc

    # 3. Evict Apple's read-only mount of this device, or Apple's mount at the
    # mount point. From the first eviction on, every exit must restore Apple's
    # mount — including an interrupt, so the sequence is guarded by
    # BaseException below.
    try:
        for entry in mounts():
            if is_fuse_t(entry):
                continue
            if entry.source == device.device or (
                entry.mount_point == mount_point
                and entry.source.startswith("/dev/")
            ):
                _unmount(entry.mount_point, run=run)

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
            mounts, mount_point, timeout=timeout, sleep=sleep
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
