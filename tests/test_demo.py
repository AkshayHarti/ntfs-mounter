import hashlib

import pytest

from ntfsmnt.demo import DemoError, attach_image, ensure_image


def write(path, data=b"image"):
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def test_existing_file_with_matching_hash_is_not_downloaded(tmp_path):
    path = tmp_path / "demo.img"
    digest = write(path, b"verified")

    def fetch(_):
        pytest.fail("must not download a verified image")

    assert ensure_image(fetch=fetch, path=path, sha256=digest) == path


def test_hash_mismatch_deletes_the_file_and_raises(tmp_path):
    path = tmp_path / "demo.img"
    write(path, b"old")

    def fetch(destination):
        destination.write_bytes(b"tampered")

    with pytest.raises(DemoError):
        ensure_image(fetch=fetch, path=path, sha256="0" * 64)

    assert not path.exists()


def test_missing_file_is_downloaded_and_verified(tmp_path):
    path = tmp_path / "nested" / "demo.img"
    digest = hashlib.sha256(b"fresh").hexdigest()

    def fetch(destination):
        destination.write_bytes(b"fresh")

    assert ensure_image(fetch=fetch, path=path, sha256=digest) == path
    assert path.read_bytes() == b"fresh"


def test_empty_pinned_hash_refuses_before_downloading(tmp_path):
    with pytest.raises(DemoError):
        ensure_image(path=tmp_path / "demo.img", sha256="",
                     fetch=lambda _: pytest.fail("must not download"))


def test_attach_uses_readonly_and_nomount(tmp_path):
    seen = {}

    def run(argv):
        seen["argv"] = argv
        return 0, "/dev/disk7\n"

    assert attach_image(tmp_path / "demo.img", run=run) == "/dev/disk7"
    assert seen["argv"][:2] == ["hdiutil", "attach"]
    assert "-readonly" in seen["argv"]
    assert "-nomount" in seen["argv"]


def test_attach_parses_the_device_from_hdiutil_output(tmp_path):
    def run(argv):
        return 0, ("/dev/disk9           GUID_partition_scheme\n"
                   "/dev/disk9s1         Microsoft Basic Data\n")

    assert attach_image(tmp_path / "demo.img", run=run) == "/dev/disk9"


def test_attach_failure_raises(tmp_path):
    with pytest.raises(DemoError):
        attach_image(tmp_path / "demo.img", run=lambda argv: (1, "busy"))


def test_attach_without_a_device_raises(tmp_path):
    with pytest.raises(DemoError):
        attach_image(tmp_path / "demo.img", run=lambda argv: (0, "attached"))


def test_cli_demo_prints_the_device(monkeypatch, capsys):
    from ntfsmnt.cli import main
    import ntfsmnt.demo as demo

    monkeypatch.setattr(demo, "ensure_image", lambda **kw: "/tmp/demo.img")
    monkeypatch.setattr(demo, "attach_image", lambda path: "/dev/disk7")

    assert main(["demo"]) == 0
    assert "/dev/disk7" in capsys.readouterr().out


def test_cli_demo_json(monkeypatch, capsys):
    import json

    from ntfsmnt.cli import main
    import ntfsmnt.demo as demo

    monkeypatch.setattr(demo, "ensure_image", lambda **kw: "/tmp/demo.img")
    monkeypatch.setattr(demo, "attach_image", lambda path: "/dev/disk7")

    assert main(["demo", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "device": "/dev/disk7", "disposable": True}


def test_cli_demo_reports_a_refusal(monkeypatch, capsys):
    from ntfsmnt.cli import main
    import ntfsmnt.demo as demo

    def refuse(**kw):
        raise demo.DemoError("not published")

    monkeypatch.setattr(demo, "ensure_image", refuse)

    assert main(["demo"]) == 1
    assert "not published" in capsys.readouterr().err


def test_mcp_demo_attaches_before_serving(monkeypatch):
    import ntfsmnt.mcp as mcp
    import ntfsmnt.demo as demo
    from ntfsmnt.cli import main

    order = []
    monkeypatch.setattr(demo, "ensure_image", lambda **kw: "/tmp/demo.img")
    monkeypatch.setattr(demo, "attach_image",
                        lambda path: order.append("attach") or "/dev/disk7")

    def serve(*args, **kwargs):
        order.append("serve")
        return 0

    monkeypatch.setattr(mcp, "serve", serve)

    assert main(["mcp", "--demo"]) == 0
    assert order == ["attach", "serve"]
