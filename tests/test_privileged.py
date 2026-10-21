import sys

import pytest

from ntfsmnt.privileged import need_root


def test_need_root_execs_sudo(monkeypatch):
    called = {}
    monkeypatch.setattr("os.geteuid", lambda: 501)
    monkeypatch.setattr("os.execvp", lambda f, a: called.update(file=f, args=a))
    # A real execvp never returns; the fake does, so the loud failure is expected.
    with pytest.raises(RuntimeError):
        need_root(["mount", "HD1"])
    assert called["file"] == "sudo"
    assert called["args"] == ["sudo", sys.executable, "-m", "ntfsmnt", "mount", "HD1"]


def test_need_root_noop_as_root(monkeypatch):
    called = []
    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr("os.execvp", lambda f, a: called.append((f, a)))
    need_root(["mount", "HD1"])
    assert called == []
