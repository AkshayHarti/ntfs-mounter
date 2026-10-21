"""Corroborate a mount with the device that serves it.

macOS reports our FUSE-T mount as ``fuse-t:/<label> on <mount point> (nfs)``:
the entry names a label, never a device. A label is only ambiguous when two
volumes share it. When exactly one NTFS volume in the snapshot carries the
label, that entry is that volume's mount and ``pgrep`` is corroboration, not a
requirement. When two or more volumes share the label, only the ``ntfs-3g``
process whose argv names the device can decide.

Every external effect goes through the injected ``run``.
"""

import re
from collections.abc import Callable
from typing import Literal

from ntfsmnt.mounttable import MountEntry

#: What a ``fuse-t:`` entry can say about one volume.
#: ``unknown`` means ``pgrep`` could not answer (missing, errored); it is not a
#: synonym for "other", because guessing either way can touch the wrong drive.
Ownership = Literal["ours", "other", "unknown"]


def pid_pattern(device: str) -> str:
    """The `pgrep -f` pattern matching the ntfs-3g process serving ``device``."""
    return f"ntfs-3g .*{re.escape(device)}( |$)"


def parse_pid(text: str) -> int | None:
    """The first integer token in ``text``, or ``None`` when there is none."""
    for token in text.split():
        try:
            return int(token)
        except ValueError:
            continue
    return None


def is_fuse_t(entry: MountEntry) -> bool:
    """Is ``entry`` a FUSE-T NFS mount (as opposed to Apple's local mount)?"""
    return entry.fs == "nfs" and entry.source.startswith("fuse-t:")


def owner(
    entry: MountEntry,
    *,
    device: str,
    label: str,
    run: Callable[[list[str]], tuple[int, str]],
    label_shared: bool = False,
) -> Ownership:
    """Which volume does this mount table entry belong to?

    ``label`` is the volume's label; it is what makes the question hard, never
    what settles it: ownership is decided by the device node and the process
    serving the mount, because a label can be shared or stale.

    ``label_shared`` says whether another NTFS volume in the snapshot carries
    the same label. ``pgrep`` exits 1 (no process names ``device``) for every
    same-label mount, so:

    - exit 0 with a pid — the process serves ``device``: ``"ours"``.
    - exit 1 with a unique label — no other volume could own the entry, so the
      missing process is a stale snapshot, not another disk: ``"ours"``.
    - exit 1 with a shared label — a same-label mount on another disk is the
      only explanation: ``"other"``.
    - anything else (127, a wrapper error) — ``"unknown"``; callers must not
      guess, and may treat it as ours only when the label is unique.
    """
    if not is_fuse_t(entry):
        return "other"
    returncode, output = run(["pgrep", "-f", pid_pattern(device)])
    pid = parse_pid(output)
    if returncode == 0 and pid is not None:
        return "ours"
    if returncode == 1:
        return "other" if label_shared else "ours"
    return "unknown"


def is_ours(
    entry: MountEntry, *, device: str, run: Callable[[list[str]], tuple[int, str]]
) -> bool:
    """Is ``entry`` strictly corroborated as served by ``device``?

    Positive corroboration only: an unanswerable ``pgrep`` is not ownership.
    :func:`owner` is what mount and unmount decisions use, because a unique
    label turns an unanswerable ``pgrep`` into an ownership fact.
    """
    return owner(entry, device=device, label="", run=run, label_shared=True) == "ours"
