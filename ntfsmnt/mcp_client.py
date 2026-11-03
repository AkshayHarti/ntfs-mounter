"""The unprivileged side of the MCP session: spawn the root controller and talk
to it over loopback.

The adapter never reads the human token. The handshake carries only the port and
an agent token, and the human token never leaves the controller process.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

CLI = "/usr/local/libexec/ntfsmnt/bin/ntfsmnt"
SUDO = "/usr/bin/sudo"


class ControllerError(Exception):
    def __init__(self, code: str, message: str, next_step: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.next_step = next_step


def _origin(base_url: str) -> str:
    parts = urlsplit(base_url)
    return f"{parts.scheme}://{parts.netloc}"


class Controller:
    def __init__(self, base_url: str, agent_token: str, *,
                 http: Any = urllib.request.urlopen, timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.agent_token = agent_token
        self._http = http
        self._timeout = timeout

    def _headers(self, extra: dict[str, str] | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.agent_token}",
            "Origin": _origin(self.base_url),
            "X-NTFSMNT": "1",
        }
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, body: dict | None = None,
                 extra: dict[str, str] | None = None) -> dict:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers=self._headers(extra),
        )
        if data is not None:
            request.add_header("Content-Type", "application/json")
        try:
            response = self._http(request, timeout=self._timeout)
            raw = response.read()
        except urllib.error.HTTPError as exc:
            raise _from_http_error(exc) from None
        except (urllib.error.URLError, OSError) as exc:
            raise ControllerError(
                "controller_unavailable",
                f"cannot reach the ntfsmnt controller: {exc}",
                "Restart the MCP client; the root session may have exited.",
            ) from None
        try:
            return json.loads(raw or b"{}")
        except json.JSONDecodeError:
            raise ControllerError(
                "controller_bad_response", "the controller sent invalid JSON"
            ) from None

    def get(self, path: str) -> dict:
        return self._request("GET", path)

    def post(self, path: str, body: dict | None = None,
             extra: dict[str, str] | None = None) -> dict:
        return self._request("POST", path, body, extra)

    def delete(self, path: str) -> dict:
        return self._request("DELETE", path)


def _from_http_error(exc: urllib.error.HTTPError) -> ControllerError:
    try:
        payload = json.loads(exc.read() or b"{}")
        error = payload.get("error", {})
        return ControllerError(
            error.get("code") or f"http_{exc.code}",
            error.get("message") or str(exc.reason or exc.code),
            error.get("next_step"),
        )
    except (json.JSONDecodeError, AttributeError, OSError):
        return ControllerError(f"http_{exc.code}", str(exc.reason or exc.code))


@dataclass
class ControllerSession:
    process: subprocess.Popen
    controller: Controller

    def close(self, *, grace: float = 5.0) -> None:
        """Close stdin so the controller exits on EOF, then SIGTERM if needed."""
        stdin = getattr(self.process, "stdin", None)
        if stdin is not None and not stdin.closed:
            try:
                stdin.close()
            except OSError:
                pass
        try:
            self.process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=max(grace, 1.0))
        except subprocess.TimeoutExpired:
            # Never SIGKILL a controller that owns disk operations. A stuck
            # child is a bug to report, not something to kill -9.
            pass


def _readline(stream, timeout: float) -> str:
    result: queue.Queue[str] = queue.Queue(maxsize=1)
    thread = threading.Thread(
        target=lambda: result.put(stream.readline()),
        name="ntfsmnt-handshake", daemon=True,
    )
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError("handshake timed out")
    try:
        return result.get_nowait()
    except queue.Empty:
        return ""


def start_controller(*, uid: int, gid: int, cli: str = CLI, sudo: str = SUDO,
                     timeout: float = 10.0, popen: Any = subprocess.Popen) -> ControllerSession:
    argv = [sudo, "-n", cli, "web", "--mcp-session", "--port", "0",
            "--owner", f"{uid}:{gid}", "--exit-on-stdin-eof"]
    process = popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True)
    try:
        line = _readline(process.stdout, timeout)
        if not line:
            raise ControllerError(
                "controller_unavailable",
                "the ntfsmnt controller exited before the handshake",
                "Check `sudo -n ntfsmnt web --help` and the privileged install.",
            )
        handshake = json.loads(line)
        port = int(handshake["port"])
        token = str(handshake["agent_token"])
    except (TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        process.terminate()
        raise ControllerError(
            "controller_unavailable",
            f"the ntfsmnt controller did not start: {exc}",
            "Run `sudo -n /usr/local/libexec/ntfsmnt/bin/ntfsmnt web` once to see why.",
        ) from None
    return ControllerSession(
        process=process,
        controller=Controller(f"http://127.0.0.1:{port}", token),
    )
