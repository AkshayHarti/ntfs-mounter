"""Safe unmount and eject.

Getting a volume back to Apple's read-only mount means tearing down whoever
holds it — usually our ntfs-3g mount on FUSE-T — without ever escalating to
SIGKILL. A busy volume is reported to the user instead of being forced; the
read-only mount is put back only after the last unmount succeeded.

Every side effect goes through the injected ``run``, ``mounts`` and ``sleep``
callables, so the whole sequence runs without touching the host. ``run`` never
raises and returns ``(returncode, combined_output)``, with stdout followed by
stderr captured together; that is also where ``pgrep``'s pid list arrives.
"""

import re
from collections.abc import Callable

from ntfsmnt.identity import is_ours, parse_pid, pid_pattern
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.volumes import Volume

BUSY_MARKERS = ("resource busy", "busy", "in use")
PARENT_DISK_SUFFIX = re.compile(r"s\d+$")


class UnmountFailed(Exception):
    """The volume could not be unmounted or ejected. The message is user-facing."""


def _is_ours(entry: MountEntry) -> bool:
    return entry.fs == "nfs" and entry.source.startswith("fuse-t:")


def _serves_volume(
    entry: MountEntry,
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
) -> bool:
    """Is this FUSE-T entry our mount for the volume?

    A label is not an identity: a same-label volume on another disk produces the
    same ``fuse-t:/<label>`` source and may even sit at the same path. The live
    snapshot settles it when it says this volume is read-write; otherwise the
    serving process must name this volume's device.
    """
    if (
        volume.state == "rw"
        and volume.mount_point is not None
        and entry.mount_point == volume.mount_point
    ):
        return True
    return is_ours(entry, device=volume.device.device, run=run)


def _mount_points(
    volume: Volume,
    entries: list[MountEntry],
    *,
    run: Callable[[list[str]], tuple[int, str]],
) -> list[str]:
    """Our mounts first, then Apple's, with each mount point listed once."""
    ours = [
        e for e in entries if _is_ours(e) and _serves_volume(e, volume, run=run)
    ]
    apple = [e for e in entries if e.source == volume.device.device]
    return list(dict.fromkeys(e.mount_point for e in ours + apple))


def has_mount(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
) -> bool:
    """Is the volume mounted — by a corroborated FUSE-T mount or by Apple?

    Callers use this instead of ``volume.state`` where a stale same-label
    ``fuse-t:`` entry in the snapshot must not count as this volume's mount.
    """
    return bool(_mount_points(volume, mounts(), run=run))


def _is_busy(*texts: str) -> bool:
    return any(marker in text.lower() for text in texts for marker in BUSY_MARKERS)


def _first_line(text: str) -> str:
    return next((line.strip() for line in text.splitlines() if line.strip()), "")


def _unmount(
    mount_point: str,
    device_path: str,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    sleep: Callable[[float], None],
) -> None:
    outputs: list[str] = []

    returncode, output = run(["diskutil", "unmount", mount_point])
    if returncode != 0:
        outputs.append(output)
        returncode, output = run(["umount", mount_point])

    if returncode != 0:
        outputs.append(output)
        if _is_busy(*outputs):
            # pgrep's pid list arrives in the combined-output slot.
            pid = parse_pid(run(["pgrep", "-f", pid_pattern(device_path)])[1])
            if pid is not None:
                run(["kill", "-TERM", str(pid)])
                sleep(1)
                returncode, output = run(["umount", mount_point])
                if returncode != 0:
                    outputs.append(output)

    if returncode == 0:
        return

    if _is_busy(*outputs):
        raise UnmountFailed(
            f"Could not unmount {mount_point}: it is still in use "
            f"({_first_line(output)}). Close anything using it and try again."
        )
    raise UnmountFailed(_first_line(output) or f"Could not unmount {mount_point}")


def unmount_volume(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    sleep: Callable[[float], None],
    keep_unmounted: bool = False,
) -> None:
    mount_points = _mount_points(volume, mounts(), run=run)
    if not mount_points:
        return

    for mount_point in mount_points:
        _unmount(mount_point, volume.device.device, run=run, sleep=sleep)

    if not keep_unmounted:
        run(["diskutil", "mount", volume.device.device])


def _parent_disk(identifier: str) -> str:
    return PARENT_DISK_SUFFIX.sub("", identifier)


def eject_volume(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    sleep: Callable[[float], None],
) -> None:
    if has_mount(volume, run=run, mounts=mounts):
        name = volume.device.label or volume.device.device
        raise UnmountFailed(f"{name} is still mounted. Unmount it first, then eject.")

    identifier = _parent_disk(volume.device.identifier)
    returncode, output = run(["diskutil", "eject", identifier])
    if returncode != 0:
        raise UnmountFailed(
            _first_line(output) or f"Could not eject {identifier}."
        )
