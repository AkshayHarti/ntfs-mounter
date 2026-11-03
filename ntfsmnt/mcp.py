"""MCP server: `ntfsmnt mcp` over stdio (JSON-RPC 2.0, 2024-11-05).

Nine tools. Three read the host and never need a grant:

  - list_volumes   the live snapshot as the CLI's `--json list` reports it
  - get_status     one volume by label, plus which mounts serve it
  - run_doctor     the environment checks

Six act, and every mutation needs a human-approved grant:

  - request_grant  ask a human; poll until approved, denied, or timed out
  - mount_volume   mount the volume named by the grant
  - unmount_volume unmount it
  - eject_volume   eject it
  - list_grants    the grants still alive
  - revoke_grant   spend one early

`recover` and `remove_hiberfile` are never grants. The adapter holds only an
agent token; the human gesture happens in a browser (or Touch ID) on the root
controller. stdout is JSON-RPC only; diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone

from ntfsmnt import __version__
from ntfsmnt.cli import _as_dict, _discover, _doctor_env
from ntfsmnt.doctor import run_checks
from ntfsmnt.mcp_client import ControllerError, start_controller
from ntfsmnt.volumes import VolumeError, resolve

PROTOCOL_VERSION = "2024-11-05"

READ_TOOLS = ("list_volumes", "get_status", "run_doctor")
ACTION_TOOLS = ("request_grant", "mount_volume", "unmount_volume",
                "eject_volume", "list_grants", "revoke_grant")
SUPPORTED_TOOLS = READ_TOOLS + ACTION_TOOLS

GRANTABLE = ("mount", "unmount", "eject")
POLL_INTERVAL = 0.25


def _volume_id(volume) -> str:
    return volume.device.uuid or f"dev-{volume.device.identifier}"


def _text(body: object) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "isError": False}


def _error_text(code: str, message: str, next_step: str | None = None) -> dict:
    body = {"error": {"code": code, "message": message, "next_step": next_step}}
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "isError": True}


def _tool_schemas() -> list[dict]:
    volume = {"type": "string",
              "description": "label, UUID, device, or mount point"}
    grant = {"type": "string", "description": "a grant id from request_grant"}
    return [
        {
            "name": "list_volumes",
            "description": "List the NTFS volumes diskutil can see, with state.",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
        {
            "name": "get_status",
            "description": "One volume by label, UUID, device, or mount point.",
            "inputSchema": {
                "type": "object",
                "properties": {"volume": volume},
                "required": ["volume"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_doctor",
            "description": "Run the environment checks and report findings. Read-only.",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
        {
            "name": "request_grant",
            "description": (
                "Ask the human to approve narrow, expiring permission for one "
                "volume. Opens the approval page and polls until decided. "
                "recover and remove_hiberfile are refused."),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "volume": volume,
                    "actions": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(GRANTABLE)},
                        "minItems": 1,
                    },
                    "ttl_seconds": {"type": "integer", "minimum": 60,
                                    "maximum": 1800, "default": 900},
                    "reason": {"type": "string"},
                },
                "required": ["volume", "actions"],
                "additionalProperties": False,
            },
        },
        {
            "name": "mount_volume",
            "description": "Mount the volume a grant names. A mount grant also "
                           "covers unmount.",
            "inputSchema": {
                "type": "object",
                "properties": {"volume": volume, "grant_id": grant},
                "required": ["volume", "grant_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "unmount_volume",
            "description": "Unmount the volume a grant names.",
            "inputSchema": {
                "type": "object",
                "properties": {"volume": volume, "grant_id": grant},
                "required": ["volume", "grant_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "eject_volume",
            "description": "Eject the volume a grant names.",
            "inputSchema": {
                "type": "object",
                "properties": {"volume": volume, "grant_id": grant},
                "required": ["volume", "grant_id"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_grants",
            "description": "List grants that are still alive.",
            "inputSchema": {"type": "object", "properties": {},
                            "additionalProperties": False},
        },
        {
            "name": "revoke_grant",
            "description": "Revoke one grant before it expires.",
            "inputSchema": {
                "type": "object",
                "properties": {"grant_id": grant},
                "required": ["grant_id"],
                "additionalProperties": False,
            },
        },
    ]


def _read_tool(name: str, arguments: dict, collect) -> dict:
    if name == "list_volumes":
        return _text({"volumes": [_as_dict(v) for v in collect()]})
    if name == "get_status":
        lookup = arguments.get("volume")
        if not isinstance(lookup, str) or not lookup:
            raise ValueError("get_status needs the `volume` argument")
        try:
            volume = resolve(lookup, collect())
        except VolumeError as exc:
            return _error_text("volume_not_found", str(exc),
                               "Call list_volumes and use an exact selector.")
        return _text({**_as_dict(volume),
                      "mounts": [asdict(m) for m in volume.mounts]})
    checks = run_checks(_doctor_env())
    return _text({"checks": [{"name": c.name, "ok": c.ok, "detail": c.detail,
                              "fix": c.fix} for c in checks],
                  "generatedAt": datetime.now(timezone.utc).isoformat()})


def _request_grant(arguments: dict, controller, client_name: str,
                   *, sleep=time.sleep, clock=time.monotonic) -> dict:
    actions = arguments.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("request_grant needs a non-empty `actions` list")
    destructive = [a for a in actions if a in ("recover", "remove_hiberfile")]
    if destructive:
        flag = destructive[0]
        return _error_text(
            "manual_required",
            f"`{flag}` is not grantable; a human runs it.",
            f"run `sudo ntfsmnt mount --{flag.replace('_', '-')}` yourself",
        )
    volume = arguments.get("volume")
    if not isinstance(volume, str) or not volume:
        raise ValueError("request_grant needs the `volume` argument")
    body = {
        "volume": volume,
        "actions": actions,
        "ttl_seconds": int(arguments.get("ttl_seconds", 900)),
        "reason": arguments.get("reason", ""),
        "client": client_name,
    }
    try:
        created = controller.post("/api/v1/grants", body)
        request_id = created["request_id"]
        wait_seconds = float(created.get("wait_seconds", 90))
        deadline = clock() + wait_seconds
        status = created.get("status", "pending")
        polled = created
        while status == "pending" and clock() < deadline:
            sleep(POLL_INTERVAL)
            polled = controller.get(f"/api/v1/grants/{request_id}")
            status = polled.get("status", "pending")
    except ControllerError as exc:
        return _error_text(exc.code, exc.message, exc.next_step)
    if status == "approved":
        return _text({"request_id": request_id, "status": "approved",
                      "grant_id": polled.get("grant_id")})
    if status == "denied":
        return _text({"request_id": request_id, "status": "denied"})
    return _text({"request_id": request_id, "status": "timeout",
                  "message": "the human did not approve in time"})


def _action_tool(name: str, arguments: dict, controller, collect) -> dict:
    lookup = arguments.get("volume")
    grant_id = arguments.get("grant_id")
    if not isinstance(lookup, str) or not lookup:
        raise ValueError(f"{name} needs the `volume` argument")
    if not isinstance(grant_id, str) or not grant_id:
        raise ValueError(f"{name} needs the `grant_id` argument")
    try:
        volume = resolve(lookup, collect())
    except VolumeError as exc:
        return _error_text("volume_not_found", str(exc),
                           "Call list_volumes and use an exact selector.")
    action = name.removesuffix("_volume")
    body = {"label": volume.device.label}
    if action == "mount":
        body.update({"recover": False, "remove_hiberfile": False})
    try:
        result = controller.post(
            f"/api/v1/volumes/{_volume_id(volume)}/{action}", body,
            extra={"X-NTFSMNT-GRANT": grant_id},
        )
    except ControllerError as exc:
        return _error_text(exc.code, exc.message, exc.next_step)
    return _text(result)


def call_tool(name: str, arguments: dict, *, collect=_discover, controller=None,
              client_name: str = "mcp", sleep=time.sleep,
              clock=time.monotonic) -> dict:
    if name not in SUPPORTED_TOOLS:
        raise ValueError(f"unknown tool {name!r}")
    if name in READ_TOOLS:
        return _read_tool(name, arguments, collect)
    if controller is None:
        return _error_text(
            "controller_unavailable",
            "the root ntfsmnt controller is not running",
            "Restart the MCP client so it can start `sudo -n ntfsmnt web`.",
        )
    if name == "request_grant":
        return _request_grant(arguments, controller, client_name,
                              sleep=sleep, clock=clock)
    if name in ("mount_volume", "unmount_volume", "eject_volume"):
        return _action_tool(name, arguments, controller, collect)
    if name == "list_grants":
        try:
            return _text(controller.get("/api/v1/grants"))
        except ControllerError as exc:
            return _error_text(exc.code, exc.message, exc.next_step)
    grant_id = arguments.get("grant_id")
    if not isinstance(grant_id, str) or not grant_id:
        raise ValueError("revoke_grant needs the `grant_id` argument")
    try:
        return _text(controller.delete(f"/api/v1/grants/{grant_id}"))
    except ControllerError as exc:
        return _error_text(exc.code, exc.message, exc.next_step)


def handle(request: object, **injections) -> dict | None:
    if not isinstance(request, dict) or request.get("id") is None:
        return None  # notifications get no reply, whatever we made of them
    identifier = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    def _reply(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    try:
        if method == "initialize":
            return _reply({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ntfsmnt", "version": __version__},
                "instructions":
                    "Read tools need no grant. Every mount, unmount, or eject "
                    "needs request_grant followed by a human approval in the "
                    "approval page or Touch ID; recover and remove_hiberfile "
                    "are never granted and must be run by a human.",
            })
        if method == "ping":
            return _reply({})
        if method == "tools/list":
            return _reply({"tools": _tool_schemas()})
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str):
                raise ValueError("tools/call needs `name`")
            arguments = params.get("arguments")
            if not isinstance(arguments, dict):
                arguments = {}
            return _reply(call_tool(name, arguments, **injections))
        return {"jsonrpc": "2.0", "id": identifier,
                "error": {"code": -32601, "message": f"unknown method {method!r}"}}
    except ValueError as exc:
        return _reply({"isError": True,
                       "content": [{"type": "text", "text": str(exc)}]})
    except Exception as exc:  # a tool bug must not kill the JSON-RPC stream
        return _reply({"isError": True,
                       "content": [{"type": "text", "text": str(exc)}]})


def serve(stdin, stdout, *, controller_factory=None, **injections) -> int:
    """One JSON-RPC request per line; the controller starts on first action."""
    if controller_factory is None:
        def controller_factory():
            return start_controller(uid=os.getuid(), gid=os.getgid())

    session = None
    controller = None
    client_name = "mcp"

    def _controller():
        nonlocal session, controller
        if controller is None:
            session = controller_factory()
            controller = getattr(session, "controller", session)
            injections["controller"] = controller
        return controller

    try:
        for line in stdin:
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                reply = {"jsonrpc": "2.0", "id": None,
                         "error": {"code": -32700, "message": "parse error"}}
            else:
                if isinstance(request, dict) and request.get("method") == "initialize":
                    info = (request.get("params") or {}).get("clientInfo") or {}
                    name = info.get("name") if isinstance(info, dict) else None
                    if isinstance(name, str) and name:
                        client_name = name
                if isinstance(request, dict) and request.get("method") == "tools/call":
                    tool = (request.get("params") or {}).get("name")
                    if tool in ACTION_TOOLS:
                        _controller()
                reply = handle(request, client_name=client_name, **injections)
            if reply is not None:
                stdout.write(json.dumps(reply) + "\n")
                stdout.flush()
    finally:
        close = getattr(session, "close", None)
        if close is not None:
            close()
    return 0


def main() -> int:
    return serve(sys.stdin, sys.stdout)
