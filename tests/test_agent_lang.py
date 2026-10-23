import pytest

from ntfsmnt.agent import events, openui
from ntfsmnt.agent.library import ACTIONS, COMPONENTS, as_json
from ntfsmnt.agent.state import agent_state, human_size


def p(text: str):
    return openui.parse_program(text)


def test_statements_parse() -> None:
    program = p('root = Root([head])\nhead = Heading("ntfsmnt", 1)\n')
    assert isinstance(program.root, openui.Call)
    assert program.root.name == "Root"
    assert program.statements["head"].args == (
        openui.Literal("ntfsmnt"), openui.Literal(1))


@pytest.mark.parametrize("text", [
    'root = Root(["bare"])',                # children must be references
    "head = Heading()\n",                   # missing required
    "root = Stack([])",                     # root must be Root
    "odd = NotAComponent()\nroot = Root([root2])\nroot2 = Stack([])",  # unknown component
    'b = Button("x", @Joint("y"))\nroot = Root([])',           # unknown action
])
def test_invalid_programs_rejected(text: str) -> None:
    assert openui.validate(p(text))


def test_valid_program() -> None:
    text = (
        'root = Root([h, b])\n'
        'b = Banner("ok", "info")\n'
        'h = Heading("ntfsmnt", 1)\n'
    )
    assert openui.validate(p(text)) == []


def test_forward_references_and_comments() -> None:
    text = (
        "root = Root([h])  // comment ignored\n"
        "h = TextContent(body, \"muted\")\n"
        'body = "hello"\n'
    )
    program = p(text)
    assert openui.validate(program) == []
    assert program.statements["body"] == openui.Literal("hello")


def test_string_escapes_round_trip() -> None:
    value = 'mount X\nunmount Y "quoted"'
    program = p(f'root = Root([c])\nc = Code({openui.quote(value)})\n')
    node = program.statements["c"].args[0]
    assert node.value == value


def test_unknown_reference_is_an_error() -> None:
    program = p("root = Root([nowhere])\n")
    assert any("nowhere" in problem for problem in openui.validate(program))


def test_cycle_rejected() -> None:
    program = p("root = Root([a])\na = b\nb = a\n")
    assert any("cycle" in problem for problem in openui.validate(program))


def test_action_arity_checked() -> None:
    program = p('root = Root([b])\nb = Button("go", @Mount("a", "b"))\n')
    assert any("Mount" in problem for problem in openui.validate(program))


def test_registry_covers_planner_use() -> None:
    assert COMPONENTS["Root"].props[0].type == "children"
    assert ACTIONS["Mount"].params == ("volumeId",)
    names = {c["name"] for c in as_json()["components"]}
    assert "VolumeCard" in names and "Button" in names


def test_events_have_agui_names() -> None:
    assert events.run_started("t", "r")["type"] == "RUN_STARTED"
    assert events.custom("opui", {"a": 1})["type"] == "CUSTOM"


def test_sse_framing() -> None:
    frame = events.sse(events.run_started("t", "r"))
    assert frame.startswith("data: {") and frame.endswith("\n\n")
    assert '"type": "RUN_STARTED"' in frame or '"type":"RUN_STARTED"' in frame


def test_sse_escapes_newlines() -> None:
    frame = events.sse(events.text_content("m", "a\nb"))
    assert "\na\nb\n" not in frame
    assert "a\\nb" in frame


def test_human_size() -> None:
    assert human_size(1) == "1 B"
    assert human_size(100_000_000) == "100.0 MB"


def test_agent_layer_has_no_privilege_imports() -> None:
    # The planner composes views; it must not carry the code that moves disks.
    import ntfsmnt.agent.planner as planner
    import ntfsmnt.mcp as mcp

    for module in (planner, mcp):
        source = open(module.__file__).read()
        for forbidden in ("ntfsmnt.mounter", "ntfsmnt.unmounter", "privileged",
                          "subprocess", "osascript"):
            assert forbidden not in source
