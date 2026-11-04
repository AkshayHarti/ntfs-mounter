"""Append-only audit log for grant and action events.

One JSON object per line. The real log is root-owned at
``/var/db/ntfsmnt/audit.jsonl``; tests and read-only modes use ``NullAudit``
so nothing ever needs root to run a test.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from ntfsmnt.control.clock import iso, utcnow

DEFAULT_PATH = Path("/var/db/ntfsmnt/audit.jsonl")


class AuditUnavailable(Exception):
    """The audit line could not be written; the caller decides whether to act."""


class NullAudit:
    def append(self, event: str, **fields: object) -> None:
        return None

    def read(self, limit: int = 200) -> list[dict]:
        return []


class AuditLog:
    def __init__(
        self,
        path: Path = DEFAULT_PATH,
        *,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()

    def append(self, event: str, **fields: object) -> None:
        record = {"ts": iso(self._clock()), "event": event, **fields}
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                fd = os.open(
                    self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600
                )
            except OSError as exc:
                raise AuditUnavailable(str(exc)) from exc
            try:
                os.write(fd, line.encode("utf-8"))
            except OSError as exc:
                raise AuditUnavailable(str(exc)) from exc
            finally:
                os.close(fd)

    def read(self, limit: int = 200) -> list[dict]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        records = []
        for line in lines[-limit:]:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a torn final line must not hide the rest
        return records
