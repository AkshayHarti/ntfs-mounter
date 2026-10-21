"""Mount orchestration: turn Apple's read-only NTFS mount into a read-write one.

macOS mounts NTFS read-only through FSKit and can remount read-only at any
moment, so getting a writable volume means evicting Apple's mount, mounting
through ntfs-3g on FUSE-T, proving the result is writable, and restoring Apple's
mount if anything fails.

Every side effect goes through the injected ``run``, ``mounts``, ``sleep`` and
``probe`` callables, so the whole sequence runs without touching the host.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

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


def _is_ours(entry: MountEntry) -> bool:
    return entry.fs == "nfs" and entry.source.startswith("fuse-t:")


def _serves_device(entry: MountEntry, device: Device, mount_point: str) -> bool:
    if entry.source == device.device or entry.mount_point == mount_point:
        return True
    return bool(device.label) and entry.source == f"fuse-t:/{device.label}"


def _has_our_mount(entries: list[MountEntry], mount_point: str) -> bool:
    return any(_is_ours(e) and e.mount_point == mount_point for e in entries)


def _ensure_mount_point(mount_point: str) -> None:
    Path(mount_point).mkdir(parents=True, exist_ok=True)


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
        if _has_our_mount(mounts(), mount_point):
            return True
        sleep(1)
    return _has_our_mount(mounts(), mount_point)


def _evict_apple_remounts(
    device_path: str,
    mount_point: str,
    *,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
) -> None:
    for entry in mounts():
        if entry.source == device_path and entry.mount_point != mount_point:
            _unmount(entry.mount_point, run=run)


def _fail(
    message: str,
    *,
    device_path: str,
    mount_point: str,
    entries: list[MountEntry],
    run: Callable[[list[str]], tuple[int, str]],
) -> NoReturn:
    """Tear down our mount, then put Apple's read-only mount back, last of all."""
    if _has_our_mount(entries, mount_point):
        run(["diskutil", "unmount", mount_point])
    run(["diskutil", "mount", device_path])
    raise MountFailed(message)


def _parse_pid(text: str) -> int | None:
    try:
        return int(text.strip().split()[0])
    except (IndexError, ValueError):
        return None


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
    probe: Callable[[str], None] | None = None,
) -> MountResult:
    device = volume.device
    mount_point = volume.mount_point or f"/Volumes/{device.label}"

    # 1. Already served by us: succeed without touching the stack.
    if any(_serves_device(e, device, mount_point) and _is_ours(e) for e in mounts()):
        return MountResult(volume=volume, mount_point=mount_point, pid=None)

    _ensure_mount_point(mount_point)

    # 2. Evict whoever holds the device (Apple's read-only mount, usually).
    for entry in mounts():
        if _serves_device(entry, device, mount_point) and not _is_ours(entry):
            _unmount(entry.mount_point, run=run)

    # 3. Wait for the device node to settle after the unmount.
    if not _wait_for_device(device.device, run=run, sleep=sleep):
        raise MountFailed(
            f"The device {device.device} for {device.label} did not reappear. "
            "Is the disk still connected?"
        )

    # 4. Mount read-write through ntfs-3g.
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

    # 5. Confirm the FUSE-T mount at the mount point actually appeared.
    if returncode != 0 or not _wait_for_mount(
        mounts, mount_point, timeout=timeout, sleep=sleep
    ):
        _fail(
            classify_mount_failure(returncode=returncode, stderr=stderr),
            device_path=device.device,
            mount_point=mount_point,
            entries=mounts(),
            run=run,
        )

    # 6. Apple can remount read-only at any moment; evict its competition, then
    # re-read the table so the failure path below decides on reality.
    _evict_apple_remounts(device.device, mount_point, mounts=mounts, run=run)
    entries = mounts()

    # 7. Prove the mount is writable, not merely present.
    try:
        (probe or _write_probe)(mount_point)
    except OSError:
        _fail(
            f"{device.label} mounted at {mount_point} but is not writable. "
            "Apple's read-only mount was restored.",
            device_path=device.device,
            mount_point=mount_point,
            entries=entries,
            run=run,
        )

    # 9. Report the ntfs-3g process serving the mount, when we can see it.
    pid = _parse_pid(run(["pgrep", "-f", device.device])[1])
    return MountResult(volume=volume, mount_point=mount_point, pid=pid)
