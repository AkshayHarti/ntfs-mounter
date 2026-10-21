from ntfsmnt.doctor import Env, hard_failures, run_checks


def make_env(**over):
    base = dict(
        path_exists=lambda p: True,
        run=lambda argv: (0, ""),
        euid=0,
        linkage=lambda p: "\t@rpath/libfuse-t.dylib",
        first_device=lambda: "/dev/disk4s1",
    )
    base.update(over)
    return Env(**base)


def test_all_good():
    checks = run_checks(make_env())
    assert hard_failures(checks) == []


def test_missing_fuset_is_hard_failure_with_fix():
    checks = run_checks(make_env(path_exists=lambda p: False))
    bad = {c.name: c for c in hard_failures(checks)}
    assert "fuse-t" in bad
    assert "brew install --cask fuse-t" in (bad["fuse-t"].fix or "")


def test_ntfs3g_not_linked_to_fuset():
    checks = run_checks(make_env(linkage=lambda p: "\t/usr/local/lib/libfuse.2.dylib"))
    assert any(c.name == "ntfs-3g" and not c.ok for c in checks)


def test_eperm_mentions_full_disk_access():
    checks = run_checks(make_env(run=lambda argv: (14, "Operation not permitted")))
    dev = next(c for c in checks if c.name == "device-access")
    assert not dev.ok
    assert "Full Disk Access" in dev.fix


def test_no_first_device_skips_device_access():
    checks = run_checks(make_env(first_device=lambda: None))
    dev = next(c for c in checks if c.name == "device-access")
    assert dev.ok
    assert "no ntfs device" in dev.detail.lower()
    assert hard_failures(checks) == []
