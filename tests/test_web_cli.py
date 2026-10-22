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


def test_bad_owner_is_validated_before_web_app_import(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ntfsmnt.web.app", None)
    assert main(["web", "--owner", "501"]) == 2
    err = capsys.readouterr().err
    assert "uid:gid" in err
    assert "ntfsmnt[web]" not in err


def test_web_passes_read_only_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_app, "run_server", lambda settings: seen.update(
        root=settings.root, read_only=settings.read_only, owner=settings.owner))
    monkeypatch.setattr("os.geteuid", lambda: 501)
    monkeypatch.delenv("SUDO_UID", raising=False)
    monkeypatch.delenv("SUDO_GID", raising=False)
    assert main(["web", "--port", "0"]) == 0
    assert seen == {"root": False, "read_only": True, "owner": None}


def test_run_server_binds_prints_and_closes(monkeypatch, capsys):
    import socket as socket_module
    import types

    from ntfsmnt.web.config import Settings

    created = []

    class FakeSocket:
        def __init__(self, *args, **kwargs):
            self.options = []
            self.bound = None
            self.closed = False
            created.append(self)

        def setsockopt(self, *args):
            self.options.append(args)

        def bind(self, address):
            self.bound = address

        def getsockname(self):
            return ("127.0.0.1", 9123)

        def close(self):
            self.closed = True

    monkeypatch.setattr(socket_module, "socket", FakeSocket)

    configs = []
    runs = []

    class FakeConfig:
        def __init__(self, app, **kwargs):
            self.app = app
            self.kwargs = kwargs
            configs.append(self)

    class FakeServer:
        def __init__(self, config):
            self.config = config

        def run(self, sockets=None):
            runs.append(sockets)

    fake_uvicorn = types.ModuleType("uvicorn")
    fake_uvicorn.Config = FakeConfig
    fake_uvicorn.Server = FakeServer
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)

    sentinel = object()
    monkeypatch.setattr(web_app, "create_app", lambda settings: sentinel)

    web_app.run_server(Settings(token="tok"))

    fake_sock = created[0]
    assert fake_sock.bound == ("127.0.0.1", 0)
    assert fake_sock.options == [
        (socket_module.SOL_SOCKET, socket_module.SO_REUSEADDR, 1)]
    assert capsys.readouterr().out == (
        "ntfsmnt web: http://127.0.0.1:9123/?token=tok\n")
    assert configs[0].app is sentinel
    assert configs[0].kwargs == {"log_level": "warning", "access_log": False}
    assert runs == [[fake_sock]]
    assert fake_sock.closed
