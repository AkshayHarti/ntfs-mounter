"""Parse, validate, and emit the OpenUI Lang subset this app renders.

The grammar is the documented one (assignment statements, positional
arguments, forward references, `@Action(...)` calls) restricted to what the
fixed library needs. The parser is strict: an unknown component or action is a
validation error, never something the renderer silently drops.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from ntfsmnt.agent.library import ACTIONS, COMPONENTS, ROOT_COMPONENT

__all__ = [
    "Call", "LangError", "ListExpr", "Literal", "Node", "Program", "Ref",
    "quote", "parse_program", "validate", "emit",
]


class LangError(ValueError):
    def __init__(self, message: str, line: int = 0, column: int = 0) -> None:
        self.line = line
        self.column = column
        where = f"line {line}" + (f", column {column}" if column else "")
        super().__init__(f"{where}: {message}" if line else message)


@dataclass(frozen=True)
class Literal:
    value: Any


@dataclass(frozen=True)
class Ref:
    name: str


@dataclass(frozen=True)
class ListExpr:
    items: tuple["Node", ...]


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple["Node", ...]
    action: bool = False


Node = Literal | Ref | ListExpr | Call


@dataclass
class Program:
    statements: dict[str, Node] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    lines: dict[str, int] = field(default_factory=dict)

    @property
    def root(self) -> Node | None:
        return self.statements.get("root")


# ── statements ────────────────────────────────────────────────────────────────

def _statements(text: str) -> list[tuple[int, str]]:
    """Split on newlines that are outside strings and brackets.

    Multi-line calls are legal, so a newline only ends a statement at depth 0.
    `//` comments are stripped outside strings.
    """
    out: list[tuple[int, str]] = []
    buffer: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    line = 1
    start = 1
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            buffer.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
            buffer.append(ch)
        elif ch in "([{":
            depth += 1
            buffer.append(ch)
        elif ch in ")]}":
            depth = max(0, depth - 1)
            buffer.append(ch)
        elif ch == "/" and i + 1 < len(text) and text[i + 1] == "/":
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        elif ch in "\r\n":
            if depth == 0:
                statement = "".join(buffer).strip()
                if statement:
                    out.append((start, statement))
                buffer = []
                start = line + 1
            else:
                buffer.append(" ")
            line += 1
        else:
            buffer.append(ch)
        i += 1
    statement = "".join(buffer).strip()
    if statement:
        out.append((start, statement))
    return out


_IDENT = re.compile(r"[A-Za-z_$][A-Za-z0-9_]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class _Tokens:
    def __init__(self, text: str, line: int) -> None:
        self.text = text
        self.line = line
        self.pos = 0
        self.items = self._scan()
        self.index = 0

    def _scan(self) -> list[tuple[str, Any, int]]:
        items: list[tuple[str, Any, int]] = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch.isspace():
                self.pos += 1
                continue
            if ch == '"':
                items.append(("string", self._string(), self.pos))
            elif ch in "=()[],@":
                items.append((ch, ch, self.pos))
                self.pos += 1
            else:
                match = _NUMBER.match(self.text, self.pos)
                if match:
                    raw = match.group()
                    value = float(raw) if "." in raw else int(raw)
                    items.append(("number", value, self.pos))
                    self.pos = match.end()
                    continue
                match = _IDENT.match(self.text, self.pos)
                if match:
                    items.append(("ident", match.group(), self.pos))
                    self.pos = match.end()
                    continue
                raise LangError(f"unexpected character {ch!r}", self.line, self.pos + 1)
        items.append(("eof", None, self.pos))
        return items

    def _string(self) -> str:
        start = self.pos
        self.pos += 1
        out: list[str] = []
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            self.pos += 1
            if ch == '"':
                return "".join(out)
            if ch != "\\":
                out.append(ch)
                continue
            if self.pos >= len(self.text):
                break
            esc = self.text[self.pos]
            self.pos += 1
            mapping = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "r": "\r",
                       "t": "\t", "b": "\b", "f": "\f"}
            if esc == "u":
                digits = self.text[self.pos:self.pos + 4]
                if len(digits) != 4 or not re.fullmatch(r"[0-9a-fA-F]{4}", digits):
                    raise LangError("bad unicode escape", self.line, start + 1)
                out.append(chr(int(digits, 16)))
                self.pos += 4
                continue
            if esc not in mapping:
                raise LangError(f"bad escape \\{esc}", self.line, self.pos)
            out.append(mapping[esc])
        raise LangError("unterminated string", self.line, start + 1)

    def peek(self) -> tuple[str, Any, int]:
        return self.items[self.index]

    def take(self) -> tuple[str, Any, int]:
        token = self.items[self.index]
        self.index += 1
        return token

    def expect(self, kind: str) -> tuple[str, Any, int]:
        token = self.take()
        if token[0] != kind:
            raise LangError(f"expected {kind!r}, got {token[0]!r}", self.line, token[2] + 1)
        return token


def _parse_expr(tokens: _Tokens) -> Node:
    kind, value, column = tokens.peek()
    if kind == "string":
        tokens.take()
        return Literal(value)
    if kind == "number":
        tokens.take()
        return Literal(value)
    if kind == "[":
        tokens.take()
        items: list[Node] = []
        if tokens.peek()[0] != "]":
            while True:
                items.append(_parse_expr(tokens))
                if tokens.peek()[0] == ",":
                    tokens.take()
                    continue
                break
        tokens.expect("]")
        return ListExpr(tuple(items))
    action = False
    if kind == "@":
        tokens.take()
        action = True
        kind, value, column = tokens.peek()
    if kind == "ident":
        tokens.take()
        if value == "true":
            return Literal(True) if not action else _bad_action(tokens)
        if value == "false":
            return Literal(False) if not action else _bad_action(tokens)
        if value == "null":
            return Literal(None) if not action else _bad_action(tokens)
        if tokens.peek()[0] == "(":
            tokens.take()
            args: list[Node] = []
            if tokens.peek()[0] != ")":
                while True:
                    args.append(_parse_expr(tokens))
                    if tokens.peek()[0] == ",":
                        tokens.take()
                        continue
                    break
            tokens.expect(")")
            return Call(value, tuple(args), action)
        if action:
            raise LangError("@ must precede a call", tokens.line, column + 1)
        return Ref(value)
    raise LangError(f"unexpected token {kind!r}", tokens.line, column + 1)


def _bad_action(tokens: _Tokens) -> Node:
    raise LangError("literal cannot be an action", tokens.line)


def parse_program(text: str) -> Program:
    program = Program()
    for line, statement in _statements(text):
        tokens = _Tokens(statement, line)
        name = tokens.expect("ident")[1]
        if not re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_]*", name):
            raise LangError("bad statement name", line)
        tokens.expect("=")
        node = _parse_expr(tokens)
        if tokens.peek()[0] != "eof":
            kind, _, column = tokens.peek()
            raise LangError(f"trailing {kind!r}", line, column + 1)
        if name in program.statements:
            program.order.remove(name)
        program.statements[name] = node
        program.order.append(name)
        program.lines[name] = line
    return program


# ── validation ────────────────────────────────────────────────────────────────

def _refs(node: Node):
    if isinstance(node, Ref):
        yield node.name
    elif isinstance(node, ListExpr):
        for item in node.items:
            yield from _refs(item)
    elif isinstance(node, Call):
        for arg in node.args:
            yield from _refs(arg)


def _is_literal(node: Node, kind: str) -> bool:
    if not isinstance(node, Literal):
        return False
    value = node.value
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "boolean":
        return isinstance(value, bool)
    return False


def _follow(node: Node, statements: dict[str, Node], hops: int = 32) -> Node | None:
    """Follow reference chains to their expression; None on a dangling chain."""
    for _ in range(hops):
        if not isinstance(node, Ref):
            return node
        node = statements.get(node.name)
        if node is None:
            return None
    return None


def _check_prop(prop, node: Node, where: str, statements: dict[str, Node]) -> list[str]:
    problems: list[str] = []
    label = f"{where}.{prop.name}"
    if prop.type == "children":
        if not isinstance(node, ListExpr):
            return [f"{label}: expected a list of references"]
        for item in node.items:
            if not isinstance(item, Ref):
                problems.append(f"{label}: children must be references")
            elif item.name not in statements:
                problems.append(f"{label}: unknown reference {item.name!r}")
        return problems
    if prop.type == "action":
        if not (isinstance(node, Call) and node.action):
            return [f"{label}: expected an @Action(...) call"]
        action = ACTIONS.get(node.name)
        if action is None:
            return [f"{label}: unknown action @{node.name}"]
        if len(node.args) != len(action.params):
            problems.append(
                f"{label}: @{node.name} takes {len(action.params)} argument(s), got {len(node.args)}")
        for arg in node.args:
            if not isinstance(arg, Literal) or not isinstance(arg.value, str):
                problems.append(f"{label}: @{node.name} arguments must be strings")
        return problems
    if prop.type == "enum":
        resolved = _follow(node, statements)
        if not _is_literal(resolved, "string") or resolved.value not in prop.values:
            return [f"{label}: expected one of {', '.join(prop.values)}"]
        return []
    resolved = _follow(node, statements)
    if resolved is None or not _is_literal(resolved, prop.type):
        return [f"{label}: expected {prop.type}"]
    return []


def validate(program: Program) -> list[str]:
    problems: list[str] = []
    root = program.statements.get("root")
    if root is None:
        return ["missing `root` statement"]
    if not (isinstance(root, Call) and not root.action and root.name == ROOT_COMPONENT):
        problems.append(f"root must be {ROOT_COMPONENT}([...])")

    for name in program.order:
        node = program.statements[name]
        if not isinstance(node, Call):
            if isinstance(node, Ref) and node.name not in program.statements:
                problems.append(f"{name}: unknown reference {node.name!r}")
            continue
        where = name
        if node.action:
            action = ACTIONS.get(node.name)
            if action is None:
                problems.append(f"{where}: unknown action @{node.name}")
            elif len(node.args) != len(action.params):
                problems.append(
                    f"{where}: @{node.name} takes {len(action.params)} argument(s), "
                    f"got {len(node.args)}")
            continue
        component = COMPONENTS.get(node.name)
        if component is None:
            problems.append(f"{where}: unknown component {node.name}")
            continue
        required = [p for p in component.props if not p.optional]
        if len(node.args) < len(required) or len(node.args) > len(component.props):
            problems.append(
                f"{where}: {node.name} takes {len(required)}..{len(component.props)} "
                f"argument(s), got {len(node.args)}")
            continue
        for prop, arg in zip(component.props, node.args):
            problems.extend(_check_prop(prop, arg, where, program.statements))

    for name in program.order:
        if isinstance(program.statements[name], Ref) and name not in program.statements:
            problems.append(f"{name}: unknown reference")

    # A cycle would make the renderer recurse forever.
    done: set[str] = set()
    def walk(name: str, path: list[str]) -> None:
        if name in done:
            return
        if name in path:
            problems.append("cycle: " + " -> ".join(path[path.index(name):] + [name]))
            return
        node = program.statements.get(name)
        if node is None:
            return
        for ref in _refs(node):
            walk(ref, path + [name])
        done.add(name)

    walk("root", [])
    return problems


# ── emission ──────────────────────────────────────────────────────────────────

def quote(value: str) -> str:
    """A Lang string literal. JSON escaping is a superset of the Lang escapes."""
    return json.dumps(value, ensure_ascii=False)


def emit(root_expr: str, statements: dict[str, str]) -> str:
    """`root` first (v0.1 entry rule), then layout → data in insertion order."""
    lines = [f"root = {root_expr}"]
    lines.extend(f"{name} = {expr}" for name, expr in statements.items())
    return "\n".join(lines) + "\n"
