"""Re-exec through sudo. The only privilege-escalation path in this program."""

import os
import sys


def need_root(argv: list[str]) -> None:
    if os.geteuid() == 0:
        return
    # sudo's env_reset strips the environment, and paths.py resolves
    # NTFSMNT_NTFS3G again inside the re-exec, so an override must survive.
    override = os.environ.get("NTFSMNT_NTFS3G")
    if override:
        command = ["sudo", "env", f"NTFSMNT_NTFS3G={override}",
                   sys.executable, "-m", "ntfsmnt", *argv]
    else:
        command = ["sudo", sys.executable, "-m", "ntfsmnt", *argv]
    os.execvp(command[0], command)
    raise RuntimeError("execvp returned")
