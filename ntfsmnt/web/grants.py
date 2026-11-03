"""Grant routes: an agent asks, a human approves, the grant is spent narrowly.

The adapter holds only the agent token. A request is not an approval; polling
reports `pending` until the human acts on the approval page.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ntfsmnt.control.clock import iso
from ntfsmnt.control.grants import GrantError
from ntfsmnt.web.auth import require_agent
from ntfsmnt.web.config import Settings
from ntfsmnt.web.service import Service, _Fail


class GrantRequestBody(BaseModel):
    volume: str = Field(min_length=1)
    actions: list[str] = Field(min_length=1)
    ttl_seconds: int = 900
    reason: str = ""
    client: str = "mcp"


def _error(status: int, code: str, message: str, next_step: str | None = None):
    return JSONResponse(
        {"error": {"code": code, "message": message, "next_step": next_step}},
        status_code=status,
    )


def _approval_url(settings: Settings, request_id: str, nonce: str) -> str:
    return f"http://{settings.host}:{settings.port}/approve/{request_id}?nonce={nonce}"


def register_grant_routes(
    app: FastAPI, settings: Settings, svc: Service, templates=None
) -> None:
    @app.post("/api/v1/grants")
    async def create_grant(request: Request, body: GrantRequestBody):
        require_agent(settings, request)
        try:
            volume = svc.grant_lookup(body.volume)
        except _Fail as fail:
            return _error(fail.error.status, fail.error.code, fail.error.message)

        destructive = [a for a in body.actions if a in ("recover", "remove_hiberfile")]
        if destructive:
            flag = destructive[0]
            return _error(
                400, "manual_required",
                f"`{flag}` is not grantable; a human runs it",
                f"run `sudo ntfsmnt mount --{flag.replace('_', '-')}` yourself",
            )

        try:
            pending = svc.grants.request(
                volume, body.actions,
                ttl_seconds=body.ttl_seconds, reason=body.reason, client=body.client,
            )
        except GrantError as exc:
            if exc.code == "too_many_requests":
                return _error(429, exc.code,
                              "too many pending approval requests; finish or deny one")
            return _error(400, exc.code, str(exc))
        except ValueError as exc:
            return _error(400, "invalid_request", str(exc))

        return {
            "request_id": pending.id,
            "status": pending.status,
            "approval_url": _approval_url(settings, pending.id, pending.nonce),
            "wait_seconds": 90,
        }

    @app.get("/api/v1/grants/{request_id}")
    async def poll_grant(request: Request, request_id: str):
        require_agent(settings, request)
        pending = svc.grants.pending(request_id)
        if pending is None:
            return _error(404, "grant_required", "no such approval request")
        body = {"request_id": pending.id, "status": pending.status}
        if pending.grant_id is not None:
            body["grant_id"] = pending.grant_id
        return body

    @app.get("/api/v1/grants")
    async def list_grants(request: Request):
        require_agent(settings, request)
        return {
            "grants": [
                {
                    "grant_id": grant.id,
                    "volume": grant.binding.label,
                    "device": grant.binding.device,
                    "actions": sorted(grant.actions),
                    "expires_at": iso(grant.expires_at),
                }
                for grant in svc.grants.active()
            ]
        }

    @app.delete("/api/v1/grants/{grant_id}")
    async def revoke_grant(request: Request, grant_id: str):
        require_agent(settings, request)
        return {"revoked": svc.grants.revoke(grant_id)}
