"""The `/agent` surface: an AG-UI stream that renders OpenUI Lang.

The agent has no privilege beyond what the session already has: it can read the
snapshot, refresh the doctor's read-only checks, and propose actions. A
generated button still goes through `POST /api/v1/volumes/{id}/{action}`, with
its cookie, `X-NTFSMNT`, Origin checks, and operation lock.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, \
    StreamingResponse

from ntfsmnt.agent import events
from ntfsmnt.agent.library import as_json
from ntfsmnt.agent.llm import LLMConfig
from ntfsmnt.agent.planner import detect_intent, plan_for
from ntfsmnt.agent.state import agent_state
from ntfsmnt.web.auth import require_mutation, require_session
from ntfsmnt.web.config import Settings, cookie_value

logger = logging.getLogger("ntfsmnt.web.agent")


def _error(status: int, code: str, message: str):
    return JSONResponse(
        {"error": {"code": code, "message": message, "next_step": None}},
        status_code=status,
    )


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text" and
            isinstance(part.get("text"), str))
    return ""


def _last_user_message(messages) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        text = _text_of(message.get("content")).strip()
        if text:
            return text[:2000]
    return ""


def _llm(settings: Settings) -> LLMConfig | None:
    if not settings.agent_llm:
        return None
    return LLMConfig(base_url=settings.agent_llm, model=settings.agent_model)


def register_agent_routes(app: FastAPI, settings: Settings, svc, templates) -> None:
    @app.get("/agent", response_class=HTMLResponse)
    async def agent_page(request: Request, token: str | None = None):
        if token is not None:
            if not hmac.compare_digest(token.encode(), settings.token.encode()):
                return HTMLResponse(
                    "<p>ntfsmnt: invalid launch token &mdash; relaunch with "
                    "<code>sudo ntfsmnt web</code>.</p>",
                    status_code=401,
                )
            response = RedirectResponse("/agent", status_code=303)
            response.set_cookie(
                settings.cookie_name, cookie_value(settings.token),
                httponly=True, samesite="strict", path="/",
            )
            return response
        try:
            require_session(settings, request)
        except HTTPException:
            return HTMLResponse(
                "<p>ntfsmnt: relaunch with <code>sudo ntfsmnt web</code>.</p>",
                status_code=401,
            )
        return templates.TemplateResponse(request, "agent.html", {"settings": settings})

    @app.get("/agent/library")
    async def library(request: Request):
        require_session(settings, request)
        return as_json()

    @app.post("/agent/run")
    async def run(request: Request):
        require_mutation(settings, request)
        try:
            payload = json.loads((await request.body()) or b"{}")
        except json.JSONDecodeError:
            return _error(400, "invalid_request", "The agent run needs a JSON body.")
        if not isinstance(payload, dict):
            return _error(400, "invalid_request", "The agent body must be an object.")
        message = _last_user_message(payload.get("messages"))
        if not message:
            return _error(400, "invalid_request",
                          "The run needs at least one user message.")

        started = events.run_started(
            thread_id=str(payload.get("threadId") or uuid.uuid4().hex),
            run_id=str(payload.get("runId") or uuid.uuid4().hex),
        )

        async def stream():
            try:
                yield events.sse(started)
                snap = await asyncio.to_thread(svc.snapshot)
                # Only the read-only doctor re-check happens inside the agent;
                # mount/unmount/eject are proposed in the UI, never executed here.
                if detect_intent(message) == "doctor":
                    await asyncio.to_thread(svc.refresh_doctor)
                    snap = await asyncio.to_thread(svc.snapshot)
                yield events.sse(events.state_snapshot(agent_state(snap)))
                plan = await asyncio.to_thread(plan_for, message, snap, llm=_llm(settings))
                message_id = uuid.uuid4().hex
                yield events.sse(events.text_start(message_id))
                yield events.sse(events.text_content(message_id, plan.narration))
                yield events.sse(events.text_end(message_id))
                yield events.sse(events.opui_delta(plan.program))
                yield events.sse(events.opui_meta(
                    plan.generator, model=plan.model, fallback=plan.fallback,
                    note=plan.note))
                yield events.sse(events.run_finished(started))
            except Exception:
                logger.exception("agent run failed")
                yield events.sse(events.run_error(
                    "The agent could not process that. The classic page still works."))

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
