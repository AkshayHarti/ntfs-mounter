"""One mutation at a time across every root server on the machine.

The web server already holds an in-process lock. This is the second half: a
``flock`` on a root-owned file so a second controller (a stale dashboard, an
MCP session, a future daemon) cannot mutate the same disk concurrently.
"""

from __future__ import annotations

import fcntl
import os
from pathlib import Path

DEFAULT_PATH = Path("/var/run/ntfsmnt.lock")


class OperationFileLock:
    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self.path = path
        self._fd: int | None = None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def __enter__(self) -> "OperationFileLock":
        if not self.acquire():
            raise BlockingIOError(f"lock busy: {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
