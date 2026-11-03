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


class _FakeServerKit:
    def __init__(self, monkeypatch):
        import socket as socket_module
        import types

        created, configs, runs = [], [], []
        outer = self
        self.servers = []

        class FakeSocket:
            def __init__(self, *a, **kw):
                self.closed = False
                created.append(self)

            def setsockopt(self, *a):
                pass

            def bind(self, address):
                self.bound = address

            def getsockname(self):
                return ("127.0.0.1", 9123)

            def close(self):
                self.closed = True

        class FakeConfig:
            def __init__(self, app, **kwargs):
                self.app, self.kwargs = app, kwargs
                configs.append(self)

        class FakeServer:
            def __init__(self, config):
                self.config = config
                self.should_exit = False
                outer.servers.append(self)

            def run(self, sockets=None):
                runs.append(sockets)

        fake_uvicorn = types.ModuleType("uvicorn")
        fake_uvicorn.Config = FakeConfig
        fake_uvicorn.Server = FakeServer
        monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
        monkeypatch.setattr(socket_module, "socket", FakeSocket)
        self.created, self.configs, self.runs = created, configs, runs


def test_mcp_session_prints_one_json_line(monkeypatch, capsys):
    import json

    from ntfsmnt.web.config import Settings

    kit = _FakeServerKit(monkeypatch)
    monkeypatch.setattr(web_app, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr(web_app, "_watch_stdin", lambda stream, exit_: None)

    web_app.run_server(Settings(token="human", mcp_session=True,
                                 agent_token="agent", exit_on_stdin_eof=True))

    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    assert json.loads(out[0]) == {
        "ntfsmnt": "mcp-session", "port": 9123, "agent_token": "agent"}


def test_mcp_session_never_prints_the_human_token(monkeypatch, capsys):
    from ntfsmnt.web.config import Settings

    _FakeServerKit(monkeypatch)
    monkeypatch.setattr(web_app, "create_app", lambda *a, **kw: object())
    monkeypatch.setattr(web_app, "_watch_stdin", lambda stream, exit_: None)

    web_app.run_server(Settings(token="human", mcp_session=True,
                                 agent_token="agent", exit_on_stdin_eof=True))

    assert "human" not in capsys.readouterr().out


def test_watch_stdin_calls_exit_on_eof():
    import os
    import threading
    import time

    read_fd, write_fd = os.pipe()
    called = threading.Event()
    stream = os.fdopen(read_fd)
    os.close(write_fd)

    web_app._watch_stdin(stream, called.set)

    assert called.wait(2)
    stream.close()


def test_web_parser_accepts_mcp_session_flags():
    args = _build_parser().parse_args(
        ["web", "--mcp-session", "--exit-on-stdin-eof"])
    assert args.mcp_session is True
    assert args.exit_on_stdin_eof is True
