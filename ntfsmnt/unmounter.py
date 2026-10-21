"""Safe unmount and eject.

Getting a volume back to Apple's read-only mount means tearing down whoever
holds it — usually our ntfs-3g mount on FUSE-T — without ever escalating to
SIGKILL. A busy volume is reported to the user instead of being forced. The
read-only mount is put back on every exit from the teardown — success, busy, or
interrupt — and the caller is told when that failed.

A ``fuse-t:`` entry names a label, not a device, so teardown resolves it with
:func:`ntfsmnt.identity.owner`. An entry for another label is another volume's
mount and is never classified for this one; with a unique label the remaining
entry is the volume's even when ``pgrep`` cannot answer; with a shared label
only the serving process can say, and an unanswerable ``pgrep`` refuses instead
of guessing.

Every side effect goes through the injected ``run``, ``mounts`` and ``sleep``
callables, so the whole sequence runs without touching the host. ``run`` never
raises and returns ``(returncode, combined_output)``, with stdout followed by
stderr captured together; that is also where ``pgrep``'s pid list arrives.
"""

import re
from collections.abc import Callable

from ntfsmnt.identity import names_label, owner, parse_pid, pid_pattern
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.volumes import Volume

BUSY_MARKERS = ("resource busy", "busy", "in use")
PARENT_DISK_SUFFIX = re.compile(r"s\d+$")


class UnmountFailed(Exception):
    """The volume could not be unmounted or ejected. The message is user-facing."""


def _classify(
    volume: Volume,
    entries: list[MountEntry],
    *,
    run: Callable[[list[str]], tuple[int, str]],
    label_shared: bool,
) -> tuple[list[str], list[str], list[str]]:
    """Split the table's FUSE-T entries into ours, another volume's, and unclear.

    Only entries whose source is ``fuse-t:/<label>`` are candidates at all: an
    entry for another label is another volume's mount, never this one's. Apple's
    entries are handled by the callers, which match them by device.

    ``unclear`` only happens when the label is shared and ``pgrep`` cannot
    answer: a unique label settles ownership on its own.
    """
    own: list[str] = []
    foreign: list[str] = []
    unclear: list[str] = []
    for entry in entries:
        if not names_label(entry, volume.device.label):
            continue
        verdict = owner(
            entry,
            device=volume.device.device,
            label=volume.device.label,
            run=run,
            label_shared=label_shared,
        )
        if verdict == "ours" or (verdict == "unknown" and not label_shared):
            own.append(entry.mount_point)
        elif verdict == "unknown":
            unclear.append(entry.mount_point)
        else:
            foreign.append(entry.mount_point)
    return own, foreign, unclear


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


def _still_ours(
    volume: Volume,
    *,
    mounts: Callable[[], list[MountEntry]],
    run: Callable[[list[str]], tuple[int, str]],
    label_shared: bool,
) -> bool:
    """Does the live table still show a FUSE-T mount of this volume?

    An unreadable table counts as "still mounted": restoring on a guess is how
    a second mount at ``/Volumes/<label> 1`` gets created.
    """
    try:
        entries = mounts()
    except Exception:
        return True
    own, _foreign, unclear = _classify(
        volume, entries, run=run, label_shared=label_shared
    )
    return bool(own or unclear)


def has_mount(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    label_shared: bool = False,
) -> bool:
    """Is the volume mounted — by a resolved FUSE-T mount or by Apple?

    Callers use this instead of ``volume.state`` where a stale same-label
    ``fuse-t:`` entry in the snapshot must not count as this volume's mount.
    With a shared label an entry ``pgrep`` cannot resolve counts as mounted:
    refusing an eject on a doubt is safe, guessing is not.
    """
    entries = mounts()
    own, _foreign, unclear = _classify(
        volume, entries, run=run, label_shared=label_shared
    )
    apple = [e for e in entries if e.source == volume.device.device]
    return bool(own or unclear or apple)


def unmount_volume(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    sleep: Callable[[float], None],
    keep_unmounted: bool = False,
    label_shared: bool = False,
) -> bool:
    """Tear down the volume's mounts and put Apple's read-only mount back.

    Returns True when the read-only mount was restored, or was not needed
    because ``keep_unmounted`` asked for the volume to stay unmounted. False
    means the teardown succeeded but the restore did not: the caller must tell
    the user the volume is still unmounted and how to restore it by hand.

    A ``fuse-t:`` entry is only torn down when it resolves to this volume. If
    the snapshot claimed the volume was read-write but the entry at that path
    belongs to another volume with the same label, nothing is unmounted and
    :class:`UnmountFailed` says to do it by hand.
    """
    entries = mounts()
    own, foreign, unclear = _classify(
        volume, entries, run=run, label_shared=label_shared
    )

    claimed = volume.mount_point if volume.state == "rw" else None
    if claimed is not None and claimed in foreign:
        raise UnmountFailed(
            f"The mount at {claimed} appears to belong to another volume with "
            f"the same label '{volume.device.label}'; it was not unmounted. "
            f"If it should be gone, unmount it manually: "
            f"sudo diskutil unmount {claimed}"
        )
    if unclear:
        raise UnmountFailed(
            f"Cannot tell whether the mount at {unclear[0]} belongs to "
            f"{volume.device.label} ({volume.device.device}): pgrep could not "
            f"identify the serving process, and the label is shared. Check "
            f"`mount` and unmount it manually: sudo diskutil unmount {unclear[0]}"
        )

    mount_points = list(
        dict.fromkeys(
            own + [e.mount_point for e in entries if e.source == volume.device.device]
        )
    )
    if not mount_points:
        return True

    error: BaseException | None = None
    try:
        for mount_point in mount_points:
            _unmount(mount_point, volume.device.device, run=run, sleep=sleep)
    except BaseException as exc:
        # Success, a busy volume, an interrupt: the restore decision is made
        # below, against a fresh table read, so a live mount is never doubled.
        error = exc

    if keep_unmounted:
        if error is not None:
            raise error
        return True

    if _still_ours(volume, mounts=mounts, run=run, label_shared=label_shared):
        # Our read-write mount is still live (a busy volume): mounting now would
        # be refused, or land at /Volumes/<label> 1 and leave a stray mount.
        if error is not None:
            raise error
        return False

    returncode, _ = run(["diskutil", "mount", volume.device.device])
    if error is not None:
        raise error
    return returncode == 0


def _parent_disk(identifier: str) -> str:
    return PARENT_DISK_SUFFIX.sub("", identifier)


def eject_volume(
    volume: Volume,
    *,
    run: Callable[[list[str]], tuple[int, str]],
    mounts: Callable[[], list[MountEntry]],
    sleep: Callable[[float], None],
    label_shared: bool = False,
) -> None:
    if has_mount(volume, run=run, mounts=mounts, label_shared=label_shared):
        name = volume.device.label or volume.device.device
        raise UnmountFailed(f"{name} is still mounted. Unmount it first, then eject.")

    identifier = _parent_disk(volume.device.identifier)
    returncode, output = run(["diskutil", "eject", identifier])
    if returncode != 0:
        raise UnmountFailed(
            _first_line(output) or f"Could not eject {identifier}."
        )
