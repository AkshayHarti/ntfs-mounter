"""Locate ntfs-3g and its probe.

`privileged.py` re-execs through `sudo`, and sudo's `env_reset` drops the
user's PATH, so a root process cannot find ntfs-3g from PATH alone. Resolution
therefore prefers the two known FUSE-T layouts before PATH: the
`/usr/local/bin` install `scripts/setup.sh` uses, then the Homebrew prefix
`/opt/homebrew/bin`, then the user's PATH (covers other installs), and finally
falls back to `/usr/local/bin/ntfs-3g` so imports never raise.

A macFUSE-linked ntfs-3g from Homebrew's other ntfs-3g formulas shares the
`/opt/homebrew/bin/ntfs-3g` name; if it wins over nothing else on PATH it will
be reported by `doctor` as not linked against FUSE-T, with the build hint.
Set `NTFSMNT_NTFS3G` to an absolute path to override everything for other
layouts.
"""

import os
import shutil

#: Ordered FUSE-T install locations, most canonical first. `DEFAULT_CANDIDATES`
#: is a module-level tuple so tests can replace it.
DEFAULT_CANDIDATES = (
    "/usr/local/bin/ntfs-3g",
    "/opt/homebrew/bin/ntfs-3g",
)
FALLBACK = "/usr/local/bin/ntfs-3g"

def resolve_ntfs3g() -> str:
    """Return the ntfs-3g binary path in the order documented above."""
    override = os.environ.get("NTFSMNT_NTFS3G")
    if override and os.path.isfile(override) and os.access(override, os.X_OK):
        return override
    for candidate in DEFAULT_CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("ntfs-3g")
    if found:
        return found
    return FALLBACK

def resolve_probe(ntfs3g: str) -> str:
    """The `ntfs-3g.probe` that ships beside the binary."""
    if os.path.isabs(ntfs3g):
        return os.path.join(os.path.dirname(ntfs3g), "ntfs-3g.probe")
    return "ntfs-3g.probe"

NTFS3G = resolve_ntfs3g()
NTFS3G_PROBE = resolve_probe(NTFS3G)
