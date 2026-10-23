import json

from ntfsmnt.mcp import PROTOCOL_VERSION, call_tool, handle, serve
from ntfsmnt.volumes import VolumeError

from tests.web_helpers import make_device, make_volume


def volumes():
    return [make_volume(label="HD1"), make_volume(label="HD2", identifier="disk5s1")]


def rpc(method, *, params=None, id=1):
    request = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        request["params"] = params
    return handle(request, collect=volumes)


def text_of(reply):
    return json.loads(reply["result"]["content"][0]["text"])


def test_initialize_announces_tools_capability() -> None:
    reply = rpc("initialize")
    assert reply["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert reply["result"]["serverInfo"]["name"] == "ntfsmnt"


def test_tools_list_is_read_only() -> None:
    tools = rpc("tools/list")["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == {"list_volumes", "get_status", "run_doctor"}
    # No tool takes anything that would change a disk: no verbs, no flags.
    for tool in tools:
        assert tool["inputSchema"].get("additionalProperties") is False
        assert "action" not in tool["inputSchema"]["properties"]


def test_list_volumes_returns_snapshots() -> None:
    text = text_of(rpc("tools/call", params={"name": "list_volumes"}))
    assert [v["label"] for v in text["volumes"]] == ["HD1", "HD2"]


def test_get_status_resolves_a_label() -> None:
    text = text_of(rpc("tools/call",
                       params={"name": "get_status",
                               "arguments": {"volume": "HD2"}}))
    assert text["label"] == "HD2"


def test_unknown_tool_is_an_error() -> None:
    reply = rpc("tools/call", params={"name": "mount", "arguments": {"volume": "HD1"}})
    assert reply["result"]["isError"] is True


def test_missing_argument_is_an_error() -> None:
    reply = rpc("tools/call", params={"name": "get_status", "arguments": {}})
    assert reply["result"]["isError"] is True


def test_unknown_method() -> None:
    reply = rpc("frobnicate")
    assert reply["error"]["code"] == -32601


def test_notifications_get_no_reply() -> None:
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_serve_answers_one_json_per_line() -> None:
    import io

    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n" +
        "not json\n" +
        json.dumps({"jsonrpc": "2.0", "method": "ping"}) + "\n"
    )
    out = io.StringIO()
    assert serve(stdin, out) == 0
    lines = out.getvalue().splitlines()
    assert len(lines) == 2  # the notification answered nothing
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == "ntfsmnt"
    assert json.loads(lines[1])["error"]["code"] == -32700  # garbage line


def test_no_privilege_escalation_imports() -> None:
    # Deeper than a name check: the module must not import the code that
    # changes disks.
    import ntfsmnt.mcp as module

    source = open(module.__file__).read()
    for forbidden in ("import ntfsmnt.mounter", "import ntfsmnt.unmounter",
                      "mosascript", "osascript"):
        assert forbidden not in source
