"""AG-UI event builders and SSE framing.

Event names follow the Agent-User Interaction Protocol. Only the types this
server actually emits are defined here; the renderer treats every other type as
ignorable, which keeps the producer and consumer independently evolvable.

Framing is the canonical `data: <json>\n\n`; the JSON carries `type`, so a
client that ignores the SSE `event:` field still works.
"""

from __future__ import annotations

import json
import time
import uuid

THREAD = "threadId"
RUN = "runId"


def _id() -> str:
    return uuid.uuid4().hex


def _event(type_: str, **fields) -> dict:
    payload = {"type": type_, "timestamp": int(time.time() * 1000), **fields}
    return payload


def run_started(thread_id: str | None = None, run_id: str | None = None) -> dict:
    return _event("RUN_STARTED", threadId=thread_id or _id(), runId=run_id or _id())


def run_finished(event: dict) -> dict:
    return _event("RUN_FINISHED", threadId=event[THREAD], runId=event[RUN])


def run_error(message: str, code: str = "agent_error") -> dict:
    return _event("RUN_ERROR", message=message[:300], code=code)


def state_snapshot(state: dict) -> dict:
    return _event("STATE_SNAPSHOT", snapshot=state)


def text_start(message_id: str | None = None) -> dict:
    return _event("TEXT_MESSAGE_START", messageId=message_id or _id(), role="assistant")


def text_content(message_id: str, delta: str) -> dict:
    return _event("TEXT_MESSAGE_CONTENT", messageId=message_id, delta=delta)


def text_end(message_id: str) -> dict:
    return _event("TEXT_MESSAGE_END", messageId=message_id)


def custom(name: str, value: dict) -> dict:
    return _event("CUSTOM", name=name, value=value)


def opui_delta(program: str) -> dict:
    """One OpenUI Lang chunk. The language is line-oriented, so the receiver
    can append deltas and re-parse complete lines as they arrive."""
    return custom("opui", {"lang": program})


def opui_meta(generator: str, *, model: str | None = None, fallback: bool = False,
                note: str | None = None) -> dict:
    value: dict = {"generator": generator, "fallback": fallback}
    if model:
        value["model"] = model
    if note:
        value["note"] = note
    return custom("opui.meta", value)


def sse(event: dict) -> str:
    return f"data: {json.dumps(event, separators=(',', ':'), ensure_ascii=False)}\n\n"
