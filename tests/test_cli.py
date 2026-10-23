import json
import re
import subprocess
import sys
import time

import pytest

import ntfsmnt.cli
from ntfsmnt.cli import main, render
from ntfsmnt.doctor import Check
from ntfsmnt.models import Device
from ntfsmnt.mounter import MountResult
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.volumes import Volume, build_volumes

OURS_RW = MountEntry(source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False)


def make_device(
    identifier="disk4s1",
    *,
    label="HD1",
    node="/dev/disk4s1",
    uuid="UUID-1",
    size=1_000_000_000,
    mount_point=None,
):
    return Device(
        identifier=identifier,
        device=node,
        label=label,
        uuid=uuid,
        size=size,
        fs_type="Windows_NTFS",
        mount_point=mount_point,
    )


def make_volume(dev, mounts=()):
    return Volume(device=dev, mounts=tuple(mounts))


def fake_volumes():
    return [make_volume(make_device())]


def stub_host(monkeypatch, table=(), serving=()):
    """Answer the live mount table and pgrep without touching the host."""

    monkeypatch.setattr(ntfsmnt.cli, "read_mount_table", lambda: list(table))

    def fake_run(argv):
        if argv[0] == "pgrep":
            for proc in serving:
                if re.search(argv[2], " ".join(proc)):
                    return (0, "4242\n")
            return (1, "")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr(ntfsmnt.cli, "real_run", fake_run)


def test_json_schema(capsys):
    rc = main(["list", "--json"], collector=fake_volumes)
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert set(data[0]) == {"device", "label", "uuid", "size", "state", "mount_point"}
    assert data[0]["device"] == "/dev/disk4s1"
    assert data[0]["size"] == 1_000_000_000


def test_list_alignment():
    volumes = [
        make_volume(make_device()),
        make_volume(
            make_device("disk4s2", label="HD-LONG", node="/dev/disk4s2", uuid="UUID-2"),
            mounts=[OURS_RW],
        ),
    ]
    lines = render(volumes, as_json=False).splitlines()
    header, rows = lines[0], lines[1:]
    state_index = header.index("STATE")
    assert len(rows) == 2
    assert [row.index("unmounted") if "unmounted" in row else row.index("rw") for row in rows] == [
        state_index,
        state_index,
    ]


def test_status_unknown_volume_exits_1(capsys):
    rc = main(["status", "NOPE"], collector=fake_volumes)
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "NOPE" in err


def test_status_reports_label_state_driver_and_device(capsys):
    rc = main(["status", "HD1"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.split() == ["HD1", "unmounted", "-", "-", "/dev/disk4s1", "-"]


def test_status_reports_fuse_t_driver_and_serving_pid(monkeypatch, capsys):
    def run(argv):
        assert argv == ["pgrep", "-f", "ntfs-3g .*/dev/disk4s1( |$)"]
        return (0, "48213\n")

    monkeypatch.setattr(ntfsmnt.cli, "real_run", run)
    volumes = [make_volume(make_device(mount_point="/Volumes/HD1"), mounts=[OURS_RW])]

    rc = main(["status", "HD1", "--json"], collector=lambda: volumes)

    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["state"] == "rw"
    assert data["driver"] == "fuse-t"
    assert data["pid"] == 48213
    assert data["mount_point"] == "/Volumes/HD1"


def test_status_text_line_names_the_driver_and_pid(monkeypatch, capsys):
    monkeypatch.setattr(ntfsmnt.cli, "real_run", lambda argv: (0, "48213\n"))
    volumes = [make_volume(make_device(mount_point="/Volumes/HD1"), mounts=[OURS_RW])]

    assert main(["status", "HD1"], collector=lambda: volumes) == 0

    assert capsys.readouterr().out.split() == [
        "HD1",
        "rw",
        "fuse-t",
        "/Volumes/HD1",
        "/dev/disk4s1",
        "48213",
    ]


def test_status_reports_fskit_for_apple_read_only(monkeypatch, capsys):
    ro = MountEntry(
        source="/dev/disk4s1", mount_point="/Volumes/HD1", fs="ntfs", read_only=True
    )
    monkeypatch.setattr(
        ntfsmnt.cli, "real_run", lambda argv: pytest.fail("ro status must not pgrep")
    )
    volumes = [make_volume(make_device(mount_point="/Volumes/HD1"), mounts=[ro])]

    rc = main(["status", "HD1", "--json"], collector=lambda: volumes)

    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert data["driver"] == "fskit"
    assert data["pid"] is None


def test_status_ambiguous_lists_candidates(capsys):
    volumes = [
        make_volume(make_device()),
        make_volume(make_device("disk5s1", node="/dev/disk5s1", uuid="UUID-5")),
    ]
    rc = main(["status", "HD1"], collector=lambda: volumes)
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "/dev/disk4s1" in err
    assert "/dev/disk5s1" in err


def test_mount_requires_confirmation_for_hiberfile(monkeypatch):
    def answer():
        return "HD2"

    root_calls = []
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    with pytest.raises(SystemExit) as exc:
        main(["mount", "HD1", "--remove-hiberfile"], collector=fake_volumes)
    assert exc.value.code == 2
    assert root_calls == []


def test_confirmation_prompt_goes_to_stderr_not_json_stdout(monkeypatch, capsys):
    prompts: list[bool] = []

    def answer():
        prompts.append(True)
        return "HD1"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: MountResult(
            volume=volume, mount_point="/Volumes/HD1", pid=42
        ),
    )

    rc = main(
        ["mount", "HD1", "--remove-hiberfile", "--json"], collector=fake_volumes
    )
    out, err = capsys.readouterr()

    assert rc == 0
    assert prompts == [True]
    assert json.loads(out) == {"mount_point": "/Volumes/HD1", "pid": 42}
    assert "Type the volume label (HD1) to confirm:" in err


def test_unprivileged_confirmed_flag_still_prompts(monkeypatch):
    # --confirmed is only trustworthy in the privileged child of the re-exec.
    monkeypatch.setattr(ntfsmnt.cli.os, "geteuid", lambda: 501)
    prompts: list[bool] = []

    def answer():
        prompts.append(True)
        return "HD1"

    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: MountResult(
            volume=volume, mount_point="/Volumes/HD1", pid=None
        ),
    )

    rc = main(
        ["mount", "HD1", "--remove-hiberfile", "--confirmed"], collector=fake_volumes
    )

    assert rc == 0
    assert prompts == [True]


def test_root_with_confirmed_does_not_prompt(monkeypatch):
    monkeypatch.setattr(ntfsmnt.cli.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        "builtins.input", lambda: pytest.fail("root --confirmed must not prompt")
    )
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: MountResult(
            volume=volume, mount_point="/Volumes/HD1", pid=None
        ),
    )

    rc = main(
        ["mount", "HD1", "--remove-hiberfile", "--confirmed"], collector=fake_volumes
    )

    assert rc == 0


def test_confirm_matching_label_reaches_need_root(monkeypatch, capsys):
    root_calls = []
    mounted = {}

    monkeypatch.setattr("builtins.input", lambda: "HD1")
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))

    def fake_mount(volume, **kwargs):
        mounted.update(kwargs)
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=42)

    monkeypatch.setattr(ntfsmnt.cli, "mount_volume", fake_mount)

    rc = main(["mount", "HD1", "--remove-hiberfile"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert root_calls == [
        ["mount", "/dev/disk4s1", "--remove-hiberfile", "--confirmed"]
    ]
    assert mounted["remove_hiberfile"] is True
    assert mounted["recover"] is False
    assert out.strip() == "mounted HD1 at /Volumes/HD1"


def test_reexec_argv_replays_every_flag(monkeypatch, capsys):
    root_calls = []
    mounted = {}

    monkeypatch.setattr("builtins.input", lambda: "HD1")
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))

    def fake_mount(volume, **kwargs):
        mounted.update(kwargs)
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=7)

    monkeypatch.setattr(ntfsmnt.cli, "mount_volume", fake_mount)

    rc = main(
        ["mount", "HD1", "--recover", "--remove-hiberfile", "--json"],
        collector=fake_volumes,
    )
    assert rc == 0
    argv = root_calls[0]
    assert argv == [
        "mount",
        "/dev/disk4s1",
        "--recover",
        "--remove-hiberfile",
        "--json",
        "--confirmed",
    ]
    capsys.readouterr()

    # Replay exactly what the sudo child receives: same flags, no second prompt.
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(ntfsmnt.cli.os, "geteuid", lambda: 0)  # the child is root
    monkeypatch.setattr(
        "builtins.input", lambda: pytest.fail("the child prompted twice")
    )
    rc = main(argv, collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert mounted["recover"] is True
    assert mounted["remove_hiberfile"] is True
    assert json.loads(out) == {"mount_point": "/Volumes/HD1", "pid": 7}


def test_unmount_reexec_forwards_keep_unmounted(monkeypatch, capsys):
    root_calls = []
    unmounted = {}
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))

    def fake_unmount(volume, **kwargs):
        unmounted.update(kwargs)
        return True

    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", fake_unmount)
    rc = main(["unmount", "HD1", "--keep-unmounted"], collector=fake_volumes)
    assert rc == 0
    argv = root_calls[0]
    assert argv == ["unmount", "/dev/disk4s1", "--keep-unmounted"]

    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    assert main(argv, collector=fake_volumes) == 0
    assert unmounted["keep_unmounted"] is True


def test_mount_uses_invoking_uid_and_gid(monkeypatch):
    calls = {}
    monkeypatch.setenv("SUDO_UID", "501")
    monkeypatch.setenv("SUDO_GID", "20")
    monkeypatch.setattr(ntfsmnt.cli.os, "getuid", lambda: 0)
    monkeypatch.setattr(ntfsmnt.cli.os, "getgid", lambda: 0)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: calls.update(kwargs)
        or MountResult(volume=volume, mount_point="/Volumes/HD1", pid=None),
    )
    rc = main(["mount", "HD1"], collector=fake_volumes)
    assert rc == 0
    assert (calls["uid"], calls["gid"]) == (501, 20)


def test_malformed_sudo_ids_fall_back_to_current(monkeypatch):
    calls = {}
    monkeypatch.setenv("SUDO_UID", "not-a-number")
    monkeypatch.setenv("SUDO_GID", "")
    monkeypatch.setattr(ntfsmnt.cli.os, "getuid", lambda: 0)
    monkeypatch.setattr(ntfsmnt.cli.os, "getgid", lambda: 0)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: calls.update(kwargs)
        or MountResult(volume=volume, mount_point="/Volumes/HD1", pid=None),
    )
    assert main(["mount", "HD1"], collector=fake_volumes) == 0
    assert (calls["uid"], calls["gid"]) == (0, 0)


def test_recover_does_not_prompt(monkeypatch):
    monkeypatch.setattr(
        "builtins.input", lambda: pytest.fail("--recover must not prompt")
    )
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: MountResult(
            volume=volume, mount_point="/Volumes/HD1", pid=None
        ),
    )
    assert main(["mount", "HD1", "--recover"], collector=fake_volumes) == 0


def test_confirm_eof_exits_2(monkeypatch):
    root_calls = []

    def no_stdin():
        raise EOFError

    monkeypatch.setattr("builtins.input", no_stdin)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    with pytest.raises(SystemExit) as exc:
        main(["mount", "HD1", "--remove-hiberfile"], collector=fake_volumes)
    assert exc.value.code == 2
    assert root_calls == []


def test_eject_refuses_mounted_volume_before_privilege(monkeypatch, capsys):
    root_calls = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    stub_host(monkeypatch, table=[OURS_RW])
    volumes = [make_volume(make_device(), mounts=[OURS_RW])]
    rc = main(["eject", "HD1"], collector=lambda: volumes)
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "Unmount it first" in err
    assert root_calls == []


def test_eject_ignores_a_same_label_mount_served_by_another_disk(monkeypatch, capsys):
    # Two volumes carry HD1; the only fuse-t:/HD1 entry is served by the other
    # one (disk6s1). Ejecting disk4s1 must not refuse on that entry.
    live = MountEntry(
        source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False
    )
    volumes = build_volumes(
        [
            make_device(),
            make_device("disk6s1", node="/dev/disk6s1", uuid="UUID-6"),
        ],
        [live],
    )
    stub_host(
        monkeypatch,
        table=[live],
        serving=[["/usr/local/bin/ntfs-3g", "/dev/disk6s1", "/Volumes/HD1", "-o", ""]],
    )
    root_calls = []
    ejected = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(
        ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: ejected.append(volume)
    )

    rc = main(["eject", "/dev/disk4s1"], collector=lambda: volumes)

    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "ejected HD1"
    assert root_calls == [["eject", "/dev/disk4s1"]]
    assert ejected


def test_mount_passes_the_shared_label_fact(monkeypatch):
    captured = {}
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)

    def fake_mount(volume, **kwargs):
        captured.update(kwargs)
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=None)

    monkeypatch.setattr(ntfsmnt.cli, "mount_volume", fake_mount)
    volumes = [
        make_volume(make_device()),
        make_volume(make_device("disk6s1", node="/dev/disk6s1", uuid="UUID-6")),
    ]

    assert main(["mount", "/dev/disk4s1"], collector=lambda: volumes) == 0
    assert captured["label_shared"] is True

    captured.clear()
    assert main(["mount", "/dev/disk4s1"], collector=fake_volumes) == 0
    assert captured["label_shared"] is False


def test_unmount_and_eject_pass_the_shared_label_fact(monkeypatch, capsys):
    unmounted = {}
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)

    def fake_unmount(volume, **kwargs):
        unmounted.update(kwargs)
        return True

    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", fake_unmount)
    volumes = [
        make_volume(make_device()),
        make_volume(make_device("disk6s1", node="/dev/disk6s1", uuid="UUID-6")),
    ]
    assert main(["unmount", "/dev/disk4s1"], collector=lambda: volumes) == 0
    assert unmounted["label_shared"] is True
    capsys.readouterr()

    stub_host(monkeypatch)
    ejected = {}
    monkeypatch.setattr(
        ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: ejected.update(kwargs)
    )
    assert main(["eject", "/dev/disk4s1"], collector=lambda: volumes) == 0
    assert ejected["label_shared"] is True


def test_eject_guard_reads_the_live_table_not_the_snapshot(monkeypatch, capsys):
    # The snapshot says read-write, but the live table is empty: the volume was
    # unmounted since discovery, so the guard must not refuse on stale state.
    stub_host(monkeypatch, table=[])
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: None)
    volumes = [make_volume(make_device(), mounts=[OURS_RW])]

    rc = main(["eject", "HD1"], collector=lambda: volumes)

    assert rc == 0
    assert "still mounted" not in capsys.readouterr().err


def test_unmount_of_a_relabelled_live_mount_reports_failure_not_success(
    monkeypatch, capsys
):
    # HD1 is live read-write at /Volumes/HD1-old, and the entry still carries
    # the label the mount was created with. Printing "unmounted HD1" and exiting
    # 0 would leave the volume read-write without saying so.
    old = MountEntry(
        source="fuse-t:/HD1-old",
        mount_point="/Volumes/HD1-old",
        fs="nfs",
        read_only=False,
    )
    volumes = build_volumes([make_device(mount_point="/Volumes/HD1-old")], [old])
    stub_host(
        monkeypatch,
        table=[old],
        serving=[
            ["/usr/local/bin/ntfs-3g", "/dev/disk4s1", "/Volumes/HD1-old", "-o", ""]
        ],
    )
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)

    rc = main(["unmount", "HD1"], collector=lambda: volumes)

    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "/Volumes/HD1-old" in err
    assert "unmounted HD1" not in err


def test_eject_of_a_relabelled_live_mount_is_refused_before_privilege(
    monkeypatch, capsys
):
    # Ejecting disk4 under a live ntfs-3g mount is destructive: the snapshot's
    # path is occupied, so refuse before the sudo path is even replayed.
    old = MountEntry(
        source="fuse-t:/HD1-old",
        mount_point="/Volumes/HD1-old",
        fs="nfs",
        read_only=False,
    )
    volumes = build_volumes([make_device(mount_point="/Volumes/HD1-old")], [old])
    stub_host(
        monkeypatch,
        table=[old],
        serving=[
            ["/usr/local/bin/ntfs-3g", "/dev/disk4s1", "/Volumes/HD1-old", "-o", ""]
        ],
    )
    root_calls = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))

    rc = main(["eject", "HD1"], collector=lambda: volumes)

    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "Unmount it first" in err
    assert root_calls == []


def test_mount_json_output(monkeypatch, capsys):
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(
        ntfsmnt.cli,
        "mount_volume",
        lambda volume, **kwargs: MountResult(volume=volume, mount_point="/Volumes/HD1", pid=42),
    )
    rc = main(["mount", "HD1", "--json"], collector=fake_volumes)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"mount_point": "/Volumes/HD1", "pid": 42}


def test_unmount_json_output(monkeypatch, capsys):
    root_calls = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", lambda volume, **kwargs: True)
    rc = main(["unmount", "HD1", "--json"], collector=fake_volumes)
    assert rc == 0
    assert root_calls == [["unmount", "/dev/disk4s1", "--json"]]
    assert json.loads(capsys.readouterr().out) == {"ok": True, "restored": True}


def test_unmount_restore_failure_reports_and_exits_1(monkeypatch, capsys):
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", lambda volume, **kwargs: False)

    rc = main(["unmount", "HD1"], collector=fake_volumes)

    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert "read-only restore failed" in err
    assert "sudo diskutil mount /dev/disk4s1" in err


def test_unmount_restore_failure_json_keeps_ok_and_reports_restored_false(
    monkeypatch, capsys
):
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", lambda volume, **kwargs: False)

    rc = main(["unmount", "HD1", "--json"], collector=fake_volumes)

    assert rc == 1
    assert json.loads(capsys.readouterr().out) == {"ok": True, "restored": False}


def test_eject_reports_success(monkeypatch, capsys):
    root_calls = []
    stub_host(monkeypatch)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: None)
    rc = main(["eject", "HD1"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert root_calls == [["eject", "/dev/disk4s1"]]
    assert out.strip() == "ejected HD1"


def test_eject_json_replay_keeps_json(monkeypatch, capsys):
    root_calls = []
    stub_host(monkeypatch)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: None)
    assert main(["eject", "HD1", "--json"], collector=fake_volumes) == 0
    argv = root_calls[0]
    assert argv == ["eject", "/dev/disk4s1", "--json"]
    capsys.readouterr()

    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    assert main(argv, collector=fake_volumes) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_discovery_failure_returns_1(capsys):
    def broken():
        raise subprocess.CalledProcessError(1, ["diskutil", "list", "-plist"])

    rc = main(["list"], collector=broken)
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.startswith("ntfsmnt: ")


def test_doctor_fails_on_hard_failure(monkeypatch, capsys):
    monkeypatch.setattr(
        ntfsmnt.cli,
        "run_checks",
        lambda env: [Check("fuse-t", False, "libfuse-t missing", "brew install --cask fuse-t")],
    )
    rc = main(["doctor"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 1
    assert "FAIL fuse-t: libfuse-t missing" in out
    assert "fix: brew install --cask fuse-t" in out


def test_doctor_passes_when_checks_pass(monkeypatch, capsys):
    monkeypatch.setattr(ntfsmnt.cli, "run_checks", lambda env: [Check("macos", True, "macOS 26")])
    rc = main(["doctor"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert "ok macos: macOS 26" in out


def test_mount_failure_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)

    def fail(volume, **kwargs):
        raise ntfsmnt.cli.MountFailed("The volume is hibernated.")

    monkeypatch.setattr(ntfsmnt.cli, "mount_volume", fail)
    rc = main(["mount", "HD1"], collector=fake_volumes)
    out, err = capsys.readouterr()
    assert rc == 1
    assert out == ""
    assert err.strip() == "ntfsmnt: The volume is hibernated."


def test_real_run_never_raises(monkeypatch):
    def boom(argv, **kwargs):
        raise OSError("no such binary")

    monkeypatch.setattr(ntfsmnt.cli.subprocess, "run", boom)
    rc, output = ntfsmnt.cli.real_run(["definitely-not-a-binary"])
    assert rc == 127
    assert "no such binary" in output


def test_real_run_does_not_wait_for_a_grandchild_holding_stdout():
    # ntfs-3g spawns the long-lived FUSE-T server before it daemonises. If that
    # child inherits a pipe, communicate() blocks until the mount goes away, so
    # a successful mount would look like a hang. A file for stdio keeps the
    # runner prompt; this reproduces the inheritance without ntfs-3g.
    code = (
        "import subprocess, sys\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])\n"
    )
    started = time.monotonic()
    rc, _ = ntfsmnt.cli.real_run([sys.executable, "-c", code])
    elapsed = time.monotonic() - started

    assert rc == 0
    assert elapsed < 2.0
