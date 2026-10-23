"""Anti-drift check: `page/opui-lang.js` parses what the planner emits.

The browser has no build step, so the renderer's parser mirrors
`ntfsmnt/agent/openui.py` by hand. This test runs the real JavaScript file
under node (skipped when node is absent) against real planner output, and
compares it with the Python parser. It exists because the renderer once
lacked list literals and every generated view silently failed to render.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from ntfsmnt.agent.openui import LangError, parse_program
from ntfsmnt.agent.planner import plan_for
from tests.test_agent_planner import snapshot, volume

PAGE = Path(__file__).resolve().parents[1] / "ntfsmnt" / "web" / "page"
HARNESS = """
const fs = require("fs");
const lang = require(process.argv[1]);
const parsed = lang.parseProgram(fs.readFileSync(process.argv[2], "utf8"));
const root = parsed.statements.get("root");
const body = root && root.node === "call" ? root.args[0] : root;
process.stdout.write(JSON.stringify({
  errors: parsed.errors,
  statements: parsed.statements.size,
  hasRoot: parsed.statements.has("root"),
  rootChildren: body && body.node === "list" ? body.items.length : -1,
}));
"""
MESSAGES = ("show me the volumes", "mount HD1", "run the doctor",
            "what can you do?")

node_only = pytest.mark.skipif(shutil.which("node") is None,
                               reason="node is not installed")


def parse_in_node(program: str, tmp_path: Path) -> dict:
    path = tmp_path / "program.opui"
    path.write_text(program, encoding="utf-8")
    done = subprocess.run(
        ["node", "-e", HARNESS, str(PAGE / "opui-lang.js"), str(path)],
        capture_output=True, text=True, timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_page_scripts_are_valid_javascript() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    for script in ("opui-lang.js", "agent.js"):
        done = subprocess.run(["node", "--check", str(PAGE / script)],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, f"{script}: {done.stderr}"


@node_only
@pytest.mark.parametrize("message", MESSAGES)
def test_node_parser_accepts_planner_output(message: str, tmp_path: Path) -> None:
    program = plan_for(message, snapshot(volume("HD1"),
                                         volume("ELEMENTS", node="disk5s1",
                                                state="rw",
                                                actions=("unmount",),
                                                mount_point="/Volumes/ELEMENTS"))
                       ).program
    reference = parse_program(program)
    result = parse_in_node(program, tmp_path)

    assert result["errors"] == []
    assert result["hasRoot"] is True
    assert result["statements"] == len(reference.statements)
    assert result["rootChildren"] == len(reference.statements["root"].args[0].items)


@node_only
def test_node_parser_handles_lists_escapes_and_multiline(tmp_path: Path) -> None:
    program = (
        'root = Stack([\n  t1,\n  kv\n])\n'
        't1 = TextContent("line one\\nline two \\u00e9")\n'
        'kv = KeyValue([kv1])\n'
        'kv1 = KeyValueRow("Device", "/dev/disk4s1")\n'
    )
    result = parse_in_node(program, tmp_path)
    assert result["errors"] == []
    assert result["rootChildren"] == 2
    parse_program(program)  # Python accepts the same text


@node_only
def test_node_parser_reports_errors_instead_of_crashing(tmp_path: Path) -> None:
    result = parse_in_node('root = Root([t1])\nt1 = TextContent("unterminated)\n',
                           tmp_path)
    assert result["errors"]
    with pytest.raises(LangError):
        parse_program('t1 = TextContent("unterminated)\n')
