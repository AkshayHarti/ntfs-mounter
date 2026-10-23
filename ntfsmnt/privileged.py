"""Re-exec through sudo. The only privilege-escalation path in this program."""

import os
import sys


def need_root(argv: list[str]) -> None:
    if os.geteuid() == 0:
        return
    os.execvp("sudo", ["sudo", sys.executable, "-m", "ntfsmnt", *argv])
    raise RuntimeError("execvp returned")
