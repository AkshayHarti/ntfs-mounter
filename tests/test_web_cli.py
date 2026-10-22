import sys

import pytest

pytest.importorskip("fastapi")

from ntfsmnt.cli import _build_parser, main
import ntfsmnt.web.app as web_app


def test_parser_accepts_web_flags():
    args = _build_parser().parse_args(
        ["web", "--port", "9000", "--open", "--owner", "501:20"])
    assert (args.command, args.port, args.open, args.owner) == ("web", 9000, True, "501:20")


def test_missing_extra_prints_install_hint(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ntfsmnt.web.app", None)
    assert main(["web"]) == 1
    assert "ntfsmnt[web]" in capsys.readouterr().err


def test_bad_owner_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(web_app, "run_server", lambda settings: None)
    assert main(["web", "--owner", "501"]) == 2
    assert "uid:gid" in capsys.readouterr().err


def test_web_passes_read_only_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_app, "run_server", lambda settings: seen.update(
        root=settings.root, read_only=settings.read_only, owner=settings.owner))
    monkeypatch.setattr("os.geteuid", lambda: 501)
    monkeypatch.setattr("os.environ", {})
    assert main(["web", "--port", "0"]) == 0
    assert seen == {"root": False, "read_only": True, "owner": None}
