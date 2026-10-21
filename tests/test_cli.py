import json

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
    assert root_calls == [["mount", "HD1"]]
    assert mounted["remove_hiberfile"] is True
    assert mounted["recover"] is False
    assert out.strip() == "mounted HD1 at /Volumes/HD1"


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
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: None)
    monkeypatch.setattr(ntfsmnt.cli, "unmount_volume", lambda volume, **kwargs: None)
    rc = main(["unmount", "HD1", "--json"], collector=fake_volumes)
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


def test_eject_reports_success(monkeypatch, capsys):
    root_calls = []
    monkeypatch.setattr(ntfsmnt.cli, "need_root", lambda argv: root_calls.append(argv))
    monkeypatch.setattr(ntfsmnt.cli, "eject_volume", lambda volume, **kwargs: None)
    rc = main(["eject", "HD1"], collector=fake_volumes)
    out = capsys.readouterr().out
    assert rc == 0
    assert root_calls == [["eject", "HD1"]]
    assert out.strip() == "ejected HD1"


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
