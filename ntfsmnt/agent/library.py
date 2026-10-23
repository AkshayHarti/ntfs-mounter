"""The closed component and action library the agent composes from.

This module is the single source of truth for three consumers: the OpenUI Lang
validator, the JSON contract served to the browser renderer, and the prompt
sent to an optional model. It is stdlib-only so the agent layer imports without
the web extra.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Prop:
    name: str
    type: str  # string | number | boolean | children | action | enum
    values: tuple[str, ...] = ()
    optional: bool = False


@dataclass(frozen=True)
class Component:
    name: str
    props: tuple[Prop, ...]
    summary: str


@dataclass(frozen=True)
class Action:
    name: str
    params: tuple[str, ...]
    summary: str


def _children() -> tuple[Prop, ...]:
    return (Prop("children", "children"),)


COMPONENTS: dict[str, Component] = {
    c.name: c
    for c in (
        Component("Root", _children(), "Entry point; wraps the whole page."),
        Component("Stack", _children(), "Vertical group."),
        Component("Row", _children(), "Horizontal group."),
        Component("Card", _children(), "Bordered group."),
        Component("ButtonRow", _children(), "Horizontal group of buttons."),
        Component("Heading", (Prop("text", "string"), Prop("level", "number", optional=True)),
                  "Section heading."),
        Component("TextContent", (Prop("text", "string"),
                                  Prop("tone", "enum", ("default", "muted", "strong"), True)),
                  "Paragraph. Tone: default, muted, strong."),
        Component("Badge", (Prop("text", "string"),
                            Prop("tone", "enum", ("neutral", "ok", "warn", "danger"), True)),
                  "Small status pill."),
        Component("Banner", (Prop("text", "string"),
                             Prop("tone", "enum", ("info", "warning", "blocker"))),
                  "Full-width message. Tone: info, warning, blocker."),
        Component("VolumeCard",
                  (Prop("label", "string"), Prop("device", "string"),
                   Prop("state", "string"), Prop("size", "string"),
                   Prop("volumeId", "string"), Prop("children", "children")),
                  "One volume: facts plus its action buttons."),
        Component("KeyValueRow", (Prop("key", "string"), Prop("value", "string")),
                  "One fact in a KeyValue list."),
        Component("KeyValue", _children(), "List of KeyValueRow."),
        Component("Button", (Prop("label", "string"), Prop("action", "action")),
                  "Runs an allow-listed action."),
        Component("Suggestion", (Prop("label", "string"), Prop("action", "action")),
                  "Chip that sends the text to the agent."),
        Component("Suggestions", _children(), "Group of Suggestion chips."),
        Component("ActivityItem",
                  (Prop("at", "string"), Prop("action", "string"),
                   Prop("summary", "string"), Prop("ok", "boolean")),
                  "One recent operation."),
        Component("ActivityList", _children(), "List of ActivityItem."),
        Component("Code", (Prop("text", "string"),), "Monospace block."),
        Component("Empty", (Prop("text", "string"),), "Placeholder for a missing list."),
    )
}

ROOT_COMPONENT = "Root"

ACTIONS: dict[str, Action] = {
    a.name: a
    for a in (
        Action("Mount", ("volumeId",), "Mount a volume read-write (asks the human to confirm)."),
        Action("Unmount", ("volumeId",), "Restore Apple's read-only mount."),
        Action("Eject", ("volumeId",), "Eject an unmounted volume."),
        Action("Doctor", (), "Re-run the host checks."),
        Action("Refresh", (), "Re-read the live mount table."),
        Action("ToAssistant", ("text",), "Send a message to the agent."),
    )
}

# Actions the renderer may dispatch to the existing HTTP routes. ToAssistant and
# Refresh stay client-side; Doctor re-runs the read-only checks through the
# agent endpoint. None of these mounts, unmounts, or ejects on its own.
VOLUME_ACTIONS = frozenset({"Mount", "Unmount", "Eject"})


def as_json() -> dict:
    return {
        "language": "openui-lang/1.0",
        "root": ROOT_COMPONENT,
        "components": [
            {
                "name": c.name,
                "summary": c.summary,
                "props": [
                    {"name": p.name, "type": p.type, "values": list(p.values),
                     "optional": p.optional}
                    for p in c.props
                ],
            }
            for c in COMPONENTS.values()
        ],
        "actions": [
            {"name": a.name, "params": list(a.params), "summary": a.summary}
            for a in ACTIONS.values()
        ],
        "volumeActions": sorted(VOLUME_ACTIONS),
    }


def prompt() -> str:
    """The system prompt for an optional model. Compact on purpose."""
    rows = []
    for component in COMPONENTS.values():
        props = ", ".join(
            f"{p.name}: {p.type}{'(' + '|'.join(p.values) + ')' if p.values else ''}"
            f"{'?' if p.optional else ''}"
            for p in component.props
        )
        rows.append(f"- {component.name}({props})")
    actions = "\n".join(
        f"- @{a.name}({', '.join(a.params)})" for a in ACTIONS.values()
    )
    return (
        "You compose a user interface in OpenUI Lang 1.0.\n"
        "Output ONLY OpenUI Lang: one `name = Expression` statement per line, "
        "the first statement must be `root = Root([...])`.\n"
        "Positional arguments only. Forward references are allowed. "
        'Strings use double quotes, newlines are \\n, null skips an optional prop.\n'
        "Use ONLY these components:\n" + "\n".join(rows) + "\n"
        "Use ONLY these actions, and only when the supplied state says the "
        "action is enabled:\n" + actions + "\n"
        "The action availability in the state is authoritative; never claim an "
        "action already happened. Do not output URLs, scripts, or markdown.\n"
    )
