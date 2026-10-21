import json
import re
import subprocess

import pytest

import ntfsmnt.cli
from ntfsmnt.cli import main, render
from ntfsmnt.doctor import Check
from ntfsmnt.models import Device
from ntfsmnt.mounter import MountResult
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.volumes import Volume

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


def test_status_reports_label_state_and_device(capsys):
    rc = main(["status", "HD1"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert out.split() == ["HD1", "unmounted", "-", "/dev/disk4s1"]


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
    def answer(_prompt):
        return "HD2"

    root_calls = []
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    with pytest.raises(SystemExit) as exc:
        main(["mount", "HD1", "--remove-hiberfile"], collector=fake_volumes)
    assert exc.value.code == 2
    assert root_calls == []


def test_confirm_matching_label_reaches_need_root(monkeypatch, capsys):
    root_calls = []
    mounted = {}

    monkeypatch.setattr("builtins.input", lambda _prompt: "HD1")
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

    monkeypatch.setattr("builtins.input", lambda _prompt: "HD1")
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
    monkeypatch.setattr(
        "builtins.input", lambda _prompt: pytest.fail("the child prompted twice")
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
        "builtins.input", lambda _prompt: pytest.fail("--recover must not prompt")
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

    def no_stdin(_prompt):
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


def test_eject_ignores_a_stale_same_label_mount(monkeypatch, capsys):
    # The volume itself is unmounted; the only fuse-t:/HD1 entry in the table
    # is served by another disk. It must not refuse this eject.
    disposed = MountEntry(
        source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False
    )
    stub_host(
        monkeypatch,
        table=[disposed],
        serving=[["/usr/local/bin/ntfs-3g", "/dev/disk9s1", "/Volumes/HD1", "-o", ""]],
    )
    root_calls = []
    ejected = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(
        ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: ejected.append(volume)
    )

    rc = main(["eject", "HD1"], collector=fake_volumes)

    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "ejected HD1"
    assert root_calls == [["eject", "/dev/disk4s1"]]
    assert ejected


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
    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", lambda volume, **kwargs: None)
    rc = main(["unmount", "HD1", "--json"], collector=fake_volumes)
    assert rc == 0
    assert root_calls == [["unmount", "/dev/disk4s1", "--json"]]
    assert json.loads(capsys.readouterr().out) == {"ok": True}


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
