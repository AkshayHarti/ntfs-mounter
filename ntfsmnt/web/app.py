import asyncio
import hmac
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ntfsmnt.web.config import Settings, cookie_matches, cookie_value
from ntfsmnt.web.schemas import (ActivityModel, ErrorResponse, StateModel,
                                 activity_model, state_model)
from ntfsmnt.web.service import Service

PAGE_DIR = Path(__file__).parent / "page"


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

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = "unauthorized" if exc.status_code == 401 else "forbidden_origin"
        return _error(exc.status_code, code, str(exc.detail))

    app.mount("/static", StaticFiles(directory=PAGE_DIR), name="static")
    return app
