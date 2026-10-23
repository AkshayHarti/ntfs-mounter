"""Read-only MCP server: `ntfsmnt mcp` over stdio (JSON-RPC 2.0, 2024-11-05).

Three tools, none of which changes anything:
  - list_volumes   the live snapshot as the CLI's `--json list` reports it
  - get_status     one volume by label, plus which mounts serve it
  - run_doctor     the environment checks

Mounting is deliberately not a tool: it needs root and a human who decides.
An agent that wants to act should point its user at the CLI or the web UI.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone

from ntfsmnt import __version__
from ntfsmnt.cli import _as_dict, _discover, _doctor_env
from ntfsmnt.doctor import run_checks
from ntfsmnt.volumes import Volume, VolumeError, build_volumes, resolve

PROTOCOL_VERSION = "2024-11-05"

SUPPORTED_TOOLS = ("list_volumes", "get_status", "run_doctor")


def _tool_schemas() -> list[dict]:
    return [
        {
            "name": "list_volumes",
            "description": "List the NTFS volumes diskutil can see, with state.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_status",
            "description": "One volume by label, UUID, device, or mount point.",
            "inputSchema": {
                "type": "object",
                "properties": {"volume": {"type": "string"}},
                "required": ["volume"],
                "additionalProperties": False,
            },
        },
        {
            "name": "run_doctor",
            "description": "Run the environment checks and report findings. Read-only.",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    ]


def _text(body: object) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}],
            "isError": False}


def call_tool(name: str, arguments: dict,
              *, collect=_discover) -> dict:
    if name not in SUPPORTED_TOOLS:
        raise ValueError(f"unknown tool {name!r}")
    if name == "list_volumes":
        return _text({"volumes": [_as_dict(v) for v in collect()]})
    if name == "get_status":
        lookup = arguments.get("volume")
        if not isinstance(lookup, str) or not lookup:
            raise ValueError("get_status needs the `volume` argument")
        volume = resolve(lookup, collect())
        if volume is None:
            raise ValueError(f"no volume matches {lookup!r}")
        return _text({**_as_dict(volume), "mounts": [asdict(m) for m in volume.mounts]})
    checks = run_checks(_doctor_env())
    return _text({"checks": [{"name": c.name, "ok": c.ok, "detail": c.detail,
                              "fix": c.fix} for c in checks],
                  "generatedAt": datetime.now(timezone.utc).isoformat()})


def handle(request: object, **injections) -> dict | None:
    if not isinstance(request, dict) or request.get("id") is None:
        return None  # notifications get no reply, whatever we made of them
    identifier = request.get("id")
    method = request.get("method")
    params = request.get("params") or {}

    def _reply(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": identifier, "result": result}

    def _fail(code: int, message: str) -> dict | None:
        if identifier is None:
            return None  # a notification error is dropped
        return _reply({"isError": True,
                       "content": [{"type": "text",
                                    "text": f"-{code}: {message}"}]})

    try:
        if method == "initialize":
            return _reply({
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ntfsmnt", "version": __version__},
                "instructions":
                    "Read-only tools over the local NTFS volumes. "
                    "Mounting is available through the web UI or the CLI.",
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
        if identifier is None:
            return None  # unknown notification
        return {"jsonrpc": "2.0", "id": identifier,
                "error": {"code": -32601, "message": f"method not found: {method!r}"}}
    except (ValueError, KeyError, VolumeError) as exc:
        return _fail(-32602, str(exc))


def serve(stdin, stdout) -> int:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "parse error"}}
        else:
            response = handle(request)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()
    return 0
