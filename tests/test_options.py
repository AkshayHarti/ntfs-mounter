from ntfsmnt.options import build_mount_args, classify_mount_failure


def test_defaults_exclude_destructive_flags():
    args = build_mount_args("/dev/disk4s1", "/Volumes/HD1", uid=501, gid=20, label="HD1")
    assert args[0] == "/usr/local/bin/ntfs-3g"
    assert args[1:3] == ["/dev/disk4s1", "/Volumes/HD1"]
    assert args[3] == "-o"
    opts = next(a for a in args if a.startswith("uid="))
    assert "recover" not in opts and "remove_hiberfile" not in opts
    assert "uid=501,gid=20,umask=022" in opts
    assert "volname=HD1" in opts


def test_flags_are_opt_in():
    args = build_mount_args("/dev/disk4s1", "/m", uid=1, gid=1, label="X", recover=True)
    assert "recover" in next(a for a in args if a.startswith("uid="))


def test_remove_hiberfile_is_opt_in():
    args = build_mount_args(
        "/dev/disk4s1", "/m", uid=1, gid=1, label="X", remove_hiberfile=True
    )
    opts = next(a for a in args if a.startswith("uid="))
    assert "remove_hiberfile" in opts
    assert "recover" not in opts


def test_classify_eperm_points_at_fda():
    msg = classify_mount_failure(
        returncode=14, stderr="Error opening '/dev/disk4s1': Operation not permitted"
    )
    assert "Full Disk Access" in msg


def test_classify_hibernated():
    msg = classify_mount_failure(
        returncode=14, stderr="Volume is hibernated. Run chkdsk /f"
    )
    assert "hibernat" in msg.lower() and "--remove-hiberfile" in msg


def test_classify_hibernated_with_trailing_eperm():
    stderr = (
        "The disk contains an unclean file system (0, 0).\n"
        "Metadata kept in Windows cache, refused to mount.\n"
        "Failed to mount '/dev/sdb1': Operation not permitted\n"
        "The NTFS partition is in an unsafe state. Please resume and shutdown\n"
        "Windows fully (no hibernation or fast restarting), or mount the volume\n"
    )
    msg = classify_mount_failure(returncode=14, stderr=stderr)
    assert "--remove-hiberfile" in msg
    assert "Full Disk Access" not in msg


def test_classify_unclean_with_trailing_eperm():
    stderr = (
        "The disk contains an unclean file system (0, 0).\n"
        "Metadata kept in Windows cache, refused to mount.\n"
        "Failed to mount '/dev/sdb1': Operation not permitted\n"
    )
    msg = classify_mount_failure(returncode=14, stderr=stderr)
    assert "--recover" in msg
    assert "Full Disk Access" not in msg


def test_classify_success_without_a_mount_names_the_timeout():
    msg = classify_mount_failure(returncode=0, stderr="", timeout=30)
    assert msg == (
        "ntfs-3g exited successfully but the mount did not appear within 30 seconds."
    )
    assert "exit code 0" not in msg


def test_classify_success_without_a_mount_without_a_timeout():
    msg = classify_mount_failure(returncode=0, stderr="")
    assert "did not appear" in msg
    assert "exit code 0" not in msg


def test_classify_success_keeps_the_nfs_helper_message():
    # FUSE-T's mount helper fails after the ntfs-3g parent has already exited 0,
    # so its stderr is the only account of why the mount never appeared.
    stderr = (
        "Version 2022.10.3\n"
        "mount_nfs: can't mount /HD1 from fuse-t onto /Volumes/HD1: "
        "Directory not empty\n"
    )
    msg = classify_mount_failure(returncode=0, stderr=stderr, timeout=30)

    assert "did not appear within 30 seconds" in msg
    assert "Directory not empty" in msg


def test_classify_busy_unclean_and_generic():
    busy = classify_mount_failure(returncode=16, stderr="Resource busy")
    assert "mounted" in busy or "open" in busy

    dirty = classify_mount_failure(returncode=14, stderr="volume is dirty")
    assert "--recover" in dirty

    generic = classify_mount_failure(returncode=19, stderr="\n  something odd happened\n")
    assert "19" in generic and "something odd happened" in generic
