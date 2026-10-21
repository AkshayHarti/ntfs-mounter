"""Corroborate a mount with the device that serves it.

macOS reports our FUSE-T mount as ``fuse-t:/<label> on <mount point> (nfs)``:
the entry names a label, never a device, and a label is not unique. The only
reliable link from that mount to a device is the ``ntfs-3g`` process serving
it, whose argv names the device node. ``pgrep -f`` finds that process; the
pattern carries a boundary so ``/dev/disk4s1`` cannot match ``/dev/disk4s11``.

Every external effect goes through the injected ``run``.
"""

import re
from collections.abc import Callable

from ntfsmnt.mounttable import MountEntry


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


def is_ours(
    entry: MountEntry, *, device: str, run: Callable[[list[str]], tuple[int, str]]
) -> bool:
    """Is ``entry`` a FUSE-T mount served by the ntfs-3g process for ``device``?

    The mount table cannot answer this on its own: any same-label volume on any
    disk produces the same ``fuse-t:/<label>`` source. Ask the process instead.
    """
    if not (entry.fs == "nfs" and entry.source.startswith("fuse-t:")):
        return False
    return parse_pid(run(["pgrep", "-f", pid_pattern(device)])[1]) is not None
