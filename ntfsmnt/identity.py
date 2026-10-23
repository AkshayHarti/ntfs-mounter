"""Corroborate a mount with the volume it names and the device that serves it.

macOS reports our FUSE-T mount as ``fuse-t:/<label> on <mount point> (nfs)``:
the entry names a label, never a device. The entry's own source is therefore
the first question: ``fuse-t:/HD2`` is never a candidate for HD1, whatever
process happens to be running. A label is only ambiguous when two volumes share
it. When exactly one NTFS volume in the snapshot carries the label, that entry
is that volume's mount and ``pgrep`` is corroboration, not a requirement. When
two or more volumes share the label, only the ``ntfs-3g`` process whose argv
names the device can decide.

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


def source_for(label: str) -> str:
    """The source a FUSE-T mount of ``label`` reports in the mount table."""
    return f"fuse-t:/{label}"


def names_label(entry: MountEntry, label: str) -> bool:
    """Does ``entry`` claim to be the FUSE-T mount of ``label``?

    A ``fuse-t:`` entry names a label and nothing else, so its source is the
    only identity it offers. ``fuse-t:/HD2`` says "HD2": it is another volume's
    mount even when the label is unique and even when ``pgrep`` finds a process.
    """
    return bool(label) and entry.fs == "nfs" and entry.source == source_for(label)


def owner(
    entry: MountEntry,
    *,
    device: str,
    label: str,
    run: Callable[[list[str]], tuple[int, str]],
    label_shared: bool = False,
) -> Ownership:
    """Which volume does this mount table entry belong to?

    ``label`` is the volume's label. The entry's own source settles candidacy:
    an entry that does not name it is another volume's mount, and no process can
    turn it into this one's. For a candidate the serving process has the last
    word, because a label can be shared or stale.

    The entry's own source comes first: only ``fuse-t:/<label>`` can be this
    volume's mount, and anything else — Apple's mount, another label — is
    ``"other"`` without asking ``pgrep``.

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
    if not names_label(entry, label):
        return "other"
    returncode, output = run(["pgrep", "-f", pid_pattern(device)])
    pid = parse_pid(output)
    if returncode == 0 and pid is not None:
        return "ours"
    if returncode == 1:
        return "other" if label_shared else "ours"
    return "unknown"

