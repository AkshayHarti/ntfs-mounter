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

def test_need_root_preserves_the_ntfsmnt_ntfs3g_override(monkeypatch):
    # sudo's env_reset drops the original environment, and paths.py resolves
    # NTFSMNT_NTFS3G again inside the re-exec, so the override must be put
    # back through `sudo env`.
    monkeypatch.setenv("NTFSMNT_NTFS3G", "/tmp/fuse-only/ntfs-3g")
    monkeypatch.setattr("os.geteuid", lambda: 501)
    called = {}
    monkeypatch.setattr("os.execvp", lambda f, a: called.update(file=f, args=a))
    with pytest.raises(RuntimeError):
        need_root(["mount", "HD1"])
    assert called["args"] == [
        "sudo", "env", "NTFSMNT_NTFS3G=/tmp/fuse-only/ntfs-3g",
        sys.executable, "-m", "ntfsmnt", "mount", "HD1",
    ]
