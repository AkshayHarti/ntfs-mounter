import json
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ntfsmnt.control.audit import AuditLog, AuditUnavailable, NullAudit


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


def test_append_writes_one_json_line_per_event(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl", clock=Clock())

    log.append("grant.approved", grant="g_1", volume="HD1")
    log.append("mount.ok", grant="g_1", mount="/Volumes/HD1")

    lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first == {
        "ts": "2026-11-03T10:00:00Z",
        "event": "grant.approved",
        "grant": "g_1",
        "volume": "HD1",
    }
    assert json.loads(lines[1])["event"] == "mount.ok"
    assert log.read() == [json.loads(line) for line in lines]


def test_file_is_created_0600(tmp_path):
    path = tmp_path / "audit.jsonl"
    AuditLog(path).append("grant.requested")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_read_returns_last_n(tmp_path):
    log = AuditLog(tmp_path / "audit.jsonl")
    for i in range(5):
        log.append("event", i=i)

    assert [entry["i"] for entry in log.read(2)] == [3, 4]


def test_missing_directory_raises_audit_unavailable(tmp_path):
    log = AuditLog(tmp_path / "nope" / "audit.jsonl")

    with pytest.raises(AuditUnavailable):
        log.append("mount.ok")


def test_read_skips_a_truncated_final_line(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append("mount.ok")
    with path.open("a") as handle:
        handle.write('{"ts": "2026-11-03T10:00:00Z", "even')

    assert [entry["event"] for entry in log.read()] == ["mount.ok"]


def test_null_audit_accepts_everything():
    log = NullAudit()

    log.append("anything", at="all")

    assert log.read() == []
