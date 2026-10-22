import asyncio
import hmac
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ntfsmnt.web.config import Settings, cookie_matches, cookie_value
from ntfsmnt.web.schemas import (ActionRequest, ActivityModel, ErrorResponse,
                                 StateModel, activity_model, error_response,
                                 result_model, state_model)
from ntfsmnt.web.service import Service

PAGE_DIR = Path(__file__).parent / "page"

logger = logging.getLogger("ntfsmnt.web")


def _error(status: int, code: str, message: str, next_step: str | None = None):
    return JSONResponse(
        {"error": {"code": code, "message": message, "next_step": next_step}},
        status_code=status,
    )


def require_session(settings: Settings, request: Request) -> None:
    if not cookie_matches(settings.token, request.cookies.get(settings.cookie_name)):
        raise HTTPException(status_code=401, detail="missing or invalid session")


def require_mutation(settings: Settings, request: Request) -> None:
    require_session(settings, request)
    if request.headers.get("x-ntfsmnt") != "1":
        raise HTTPException(status_code=403, detail="missing X-NTFSMNT header")
    if request.headers.get("origin") not in settings.allowed_origins:
        raise HTTPException(status_code=403, detail="bad origin")


def create_app(settings: Settings, *, service: Service | None = None) -> FastAPI:
    svc = service or Service(settings)
    app = FastAPI(title="ntfsmnt web", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def host_guard(request: Request, call_next):
        if request.headers.get("host", "") not in settings.allowed_hosts:
            return _error(421, "forbidden_host", "This server only answers on loopback.")
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    async def page(request: Request, token: str | None = None):
        if token is not None:
            if not hmac.compare_digest(token.encode(), settings.token.encode()):
                return _error(401, "unauthorized", "Invalid launch token.")
            response = RedirectResponse("/", status_code=303)
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
        await asyncio.to_thread(svc.refresh_doctor)
        return HTMLResponse("<h1>ntfsmnt</h1>")

    @app.get("/api/v1/state", response_model=StateModel)
    async def state(request: Request):
        require_session(settings, request)
        return state_model(await asyncio.to_thread(svc.snapshot))

    @app.get("/api/v1/activity", response_model=ActivityModel)
    async def activity(request: Request):
        require_session(settings, request)
        return activity_model(svc.activity())

    ACTIONS = {
        "mount": lambda target, body: svc.mount(
            target, label=body.label, size=body.size, recover=body.recover,
            remove_hiberfile=body.remove_hiberfile, confirm=body.confirm),
        "unmount": lambda target, body: svc.unmount(
            target, label=body.label, size=body.size),
        "eject": lambda target, body: svc.eject(
            target, label=body.label, size=body.size),
    }

    @app.post("/api/v1/volumes/{volume_id}/{action}")
    async def act(volume_id: str, action: str, body: ActionRequest, request: Request):
        try:
            require_mutation(settings, request)
        except HTTPException as exc:
            code = "unauthorized" if exc.status_code == 401 else "forbidden_origin"
            return _error(exc.status_code, code, exc.detail)
        if action not in ACTIONS:
            return _error(404, "not_found", f"Unknown action {action!r}.")
        try:
            result = await asyncio.to_thread(ACTIONS[action], volume_id, body)
        except Exception:
            logger.exception("action %s on %s failed", action, volume_id)
            return _error(500, "internal", "Unexpected error; see the server log.")
        await _publish_state(svc)
        if result.ok:
            return result_model(result)
        error = result.error
        return JSONResponse(
            error_response(error).model_dump(), status_code=error.status)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = "unauthorized" if exc.status_code == 401 else "forbidden_origin"
        return _error(exc.status_code, code, str(exc.detail))

    app.mount("/static", StaticFiles(directory=PAGE_DIR), name="static")
    return app


async def _publish_state(svc: Service) -> None:
    snap = await asyncio.to_thread(svc.snapshot)
    svc.events.publish("state", state_model(snap).model_dump(mode="json"))
