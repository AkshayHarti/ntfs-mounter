"""The agent: route a user message to a plan, then compile it to OpenUI Lang.

The planner is deterministic by default — no model, no network, no key. An
optional LLM (`--agent-llm`) only composes the interface from the closed
library; narration, intent, and action availability always come from the code
and the live snapshot, so a hallucinating model cannot make a disabled action
clickable or claim an operation it never ran.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass

from ntfsmnt.agent import openui
from ntfsmnt.agent.llm import LLMConfig, LLMUnavailable, completion
from ntfsmnt.agent.library import prompt
from ntfsmnt.agent.state import agent_state, human_size
from ntfsmnt.web.models import Snapshot, VolumeView

__all__ = ["Plan", "detect_intent", "plan_for"]

_INTENTS = (
    ("eject", re.compile(r"\b(eject|unplug|release)\b")),
    ("unmount", re.compile(r"\b(unmount|un-mount|restore)\b|read[- ]only mode")),
    ("mount", re.compile(r"\bmount\b|read[- ]write|\brw\b|writable")),
    ("doctor", re.compile(r"\b(doctor|health|diagnos\w*|check)\b")),
    ("help", re.compile(r"\b(help|what can you do|capabilit\w+|how do i|\?)\b")),
)

_STATE_LABELS = {
    "rw": "Read-write (fuse-t)",
    "ro": "Read-only (Apple)",
    "unmounted": "Unmounted",
}


@dataclass(frozen=True)
class Plan:
    intent: str
    narration: str
    program: str
    generator: str = "deterministic"
    model: str | None = None
    fallback: bool = False
    note: str | None = None


def detect_intent(message: str) -> str:
    text = message.lower()
    for intent, pattern in _INTENTS:
        if pattern.search(text):
            return intent
    return "overview"


def _matching_volumes(message: str, volumes: tuple[VolumeView, ...]) -> list[VolumeView]:
    text = message.lower()
    matched = []
    for volume in volumes:
        keys = {volume.label.lower(), (volume.uuid or "").lower(),
                volume.device.lower()}
        if volume.mount_point:
            keys.add(volume.mount_point.lower())
        if any(key and key in text for key in keys):
            matched.append(volume)
    return matched


def _enabled(volume: VolumeView) -> dict[str, bool]:
    return {action.name: action.enabled for action in volume.actions}


class _Program:
    """Accumulates statements; `emit` writes `root` first, then the rest."""

    def __init__(self) -> None:
        self.stmts: dict[str, str] = {}
        self._n = 0

    def add(self, expr: str, prefix: str = "n") -> str:
        name = f"{prefix}{self._n}"
        self._n += 1
        self.stmts[name] = expr
        return name

    def text(self, value: str, tone: str | None = None) -> str:
        extra = f", {openui.quote(tone)}" if tone else ""
        return self.add(f"TextContent({openui.quote(value)}{extra})", "t")

    def button(self, label: str, action: str) -> str:
        return self.add(f"Button({openui.quote(label)}, {action})", "b")

    def banner(self, text: str, tone: str) -> str:
        return self.add(f"Banner({openui.quote(text)}, {openui.quote(tone)})", "ba")

    def emit(self, root_expr: str) -> str:
        return openui.emit(root_expr, self.stmts)


def _volume_card(p: _Program, volume: VolumeView, *, detail: bool) -> str:
    enabled = _enabled(volume)
    buttons = []
    if enabled.get("mount"):
        buttons.append(p.button("Mount read-write", f"@Mount({openui.quote(volume.id)})"))
    if enabled.get("unmount"):
        buttons.append(p.button("Unmount", f"@Unmount({openui.quote(volume.id)})"))
    if enabled.get("eject"):
        buttons.append(p.button("Eject", f"@Eject({openui.quote(volume.id)})"))
    state = _STATE_LABELS.get(volume.state, volume.state)
    children: list[str] = []
    if detail:
        rows = [p.add(f"KeyValueRow({openui.quote(key)}, {openui.quote(value)})", "kv")
                for key, value in (
                    ("Device", volume.device),
                    ("UUID", volume.uuid or "—"),
                    ("State", state),
                    ("Size", human_size(volume.size)),
                    ("Mounted at", volume.mount_point or "—"),
                )]
        children.append(p.add(f"KeyValue([{', '.join(rows)}])", "kvs"))
    if buttons:
        children.append(p.add(f"ButtonRow([{', '.join(buttons)}])", "row"))
    return p.add(
        f"VolumeCard({openui.quote(volume.label)}, {openui.quote(volume.device)}, "
        f"{openui.quote(state)}, {openui.quote(human_size(volume.size))}, "
        f"{openui.quote(volume.id)}, [{', '.join(children)}])",
        "v",
    )


_SUGGESTIONS = (
    ("Check the host", "@Doctor()"),
    ("Refresh from the mount table", "@Refresh()"),
    ("What can you do?", "@ToAssistant(\"what can you do\")"),
)


def _suggestions(p: _Program, extra: list[tuple[str, str]] | None = None) -> str:
    refs = [p.add(f"Suggestion({openui.quote(label)}, {action})", "s")
            for label, action in (list(extra or []) + list(_SUGGESTIONS))]
    return p.add(f"Suggestions([{', '.join(refs)}])", "sg")


def _header(p: _Program, snapshot: Snapshot) -> str:
    heading = p.add('Heading("ntfsmnt", 1)', "h")
    if snapshot.host.root and not snapshot.host.read_only:
        badge = p.add('Badge("read-write", "ok")', "bd")
    else:
        badge = p.add('Badge("read-only", "warn")', "bd")
    return p.add(f"Row([{heading}, {badge}])", "hdr")


def _doctor_banners(snapshot: Snapshot, p: _Program) -> list[str]:
    out = []
    for finding in snapshot.doctor.findings:
        out.append(p.banner(f"{finding.check}: {finding.message}", finding.severity))
        if finding.fix:
            out.append(p.text(f"Fix: {finding.fix}", "muted"))
    return out


def _summary(snapshot: Snapshot) -> str:
    total = len(snapshot.volumes)
    if total == 0:
        return "No NTFS volumes are connected."
    rw = sum(1 for v in snapshot.volumes if v.state == "rw")
    ro = sum(1 for v in snapshot.volumes if v.state == "ro")
    un = total - rw - ro
    parts = [f"{count} {label}" for count, label in (
        (rw, "mounted read-write"), (ro, "mounted read-only"),
        (un, "unmounted")) if count]
    return f"{total} NTFS volume{'s' if total != 1 else ''}: " + ", ".join(parts) + "."


def _verdict(intent: str, volume: VolumeView) -> str:
    if not _enabled(volume).get(intent):
        reason = next((a.reason for a in volume.actions
                       if a.name == intent and a.reason), None)
        return (f"{volume.label} cannot be {intent}ed right now"
                + (f": {reason}." if reason else "."))
    return f"{volume.label} is ready to {intent}. Confirm below — I do not run disk operations on my own."


def _build(intent: str, message: str, snapshot: Snapshot,
           matches: list[VolumeView]) -> Plan:
    p = _Program()
    header = _header(p, snapshot)
    banners = _doctor_banners(snapshot, p)
    volumes = list(snapshot.volumes)

    if intent == "help":
        narrate = p.text(
            "I read the live mount table and compose this page from it. I can "
            "show the volumes, check the host, and prepare mount, unmount, or "
            "eject actions — you confirm each one; I never run a disk "
            "operation on my own.", "strong")
        code = p.add("Code(" + openui.quote(
            "mount ELEMENTS      # read-write via FUSE-T\n"
            "unmount ELEMENTS    # restore Apple's read-only mount\n"
            "eject ELEMENTS      # spinning down\n") + ")", "c")
        chips = _suggestions(p)
        root = f"Root([{header}, {narrate}, {code}, {chips}])"
        return Plan("help", "Here is what I can do.", p.emit(root))

    if intent == "doctor":
        entries = list(banners) or [
            p.text(f"All host checks passed (checked {snapshot.doctor.checked_at}).",
                   "strong")]
        recheck = p.button("Re-check", "@Doctor()")
        chips = _suggestions(p)
        root = f"Root([{header}, {', '.join(entries)}, {recheck}, {chips}])"
        if snapshot.doctor.status == "ok":
            narration = "All host checks passed."
        else:
            narration = f"{len(snapshot.doctor.findings)} finding(s) — see the page below."
        return Plan("doctor", narration, p.emit(root))

    if matches:
        focused = matches
    elif intent in ("mount", "unmount", "eject") and len(volumes) == 1:
        focused = volumes  # "mount it" with a single volume means that one
    else:
        focused = volumes

    cards = [_volume_card(p, v, detail=len(focused) == 1) for v in focused]

    if intent in ("mount", "unmount", "eject"):
        if not volumes:
            narration = f"No NTFS volumes are connected, so there is nothing to {intent}."
        elif len(matches) == 1:
            narration = _verdict(intent, matches[0])
        elif not matches:
            narration = f"Which volume should I {intent}? Here are the ones I can see."
        else:
            narration = (f"{len(matches)} volumes match that. Pick the one you mean.")
    else:
        narration = _summary(snapshot)

    chips = _suggestions(p)
    empty = p.text("No NTFS volumes are connected.", "muted")
    body = ", ".join(cards) if cards else empty
    middle = banners + [body]
    root = f"Root([{header}, {', '.join(middle)}, {chips}])"
    return Plan(intent, narration, p.emit(root))


# ── optional model path ───────────────────────────────────────────────────────

def plan_for(message: str, snapshot: Snapshot, *, llm: LLMConfig | None = None) -> Plan:
    intent = detect_intent(message)
    matches = _matching_volumes(message, snapshot.volumes)
    plan = _build(intent, message, snapshot, matches)

    if llm is None:
        return plan

    try:
        user = f"State:\n{json.dumps(agent_state(snapshot), ensure_ascii=False)}\n\n" \
               f"Message:\n{message}"
        raw = completion(llm, prompt(), user)
        program = openui.parse_program(raw)
        problems = openui.validate(program)
        if problems:
            raise LLMUnavailable(f"model program rejected: {problems[0]}")
        return dataclasses.replace(plan, program=raw.strip() + "\n",
                                   generator="llm", model=llm.model)
    except LLMUnavailable as exc:
        return dataclasses.replace(plan, generator="deterministic",
                                   fallback=True, note=str(exc))
