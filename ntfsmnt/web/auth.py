"""The HTTP auth boundary, shared by the classic page and the agent page."""

from __future__ import annotations

import hmac

from fastapi import HTTPException, Request

from ntfsmnt.web.config import Settings, cookie_matches


def is_agent_request(request: Request) -> bool:
    scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
    return scheme.lower() == "bearer" and bool(credential)


def require_agent(settings: Settings, request: Request) -> None:
    """Accept only the agent bearer token; never fall back to the session cookie."""
    scheme, _, credential = (request.headers.get("authorization") or "").partition(" ")
    expected = settings.agent_token
    if scheme.lower() != "bearer" or not credential or not expected:
        raise HTTPException(status_code=401, detail="agent token required")
    if not hmac.compare_digest(credential.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="agent token required")


def require_session(settings: Settings, request: Request) -> None:
    if not cookie_matches(settings.token, request.cookies.get(settings.cookie_name)):
        raise HTTPException(status_code=401, detail="missing or invalid session")


def require_mutation(settings: Settings, request: Request) -> None:
    require_session(settings, request)
    if request.headers.get("x-ntfsmnt") != "1":
        raise HTTPException(status_code=403, detail="missing X-NTFSMNT header")
    if request.headers.get("origin") not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="bad origin")
