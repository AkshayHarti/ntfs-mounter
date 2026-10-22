import asyncio
import hmac
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import parse_qsl

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ntfsmnt.web.config import Settings, cookie_matches, cookie_value
from ntfsmnt.web.events import EventBus
from ntfsmnt.web.schemas import (ActionRequest, ActivityModel, ErrorResponse,
                                 StateModel, activity_model, error_response,
                                 result_model, state_model)
from ntfsmnt.web.service import Service

PAGE_DIR = Path(__file__).parent / "page"

templates = Jinja2Templates(directory=PAGE_DIR)

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


_FORM_TRUE = {"on", "true", "1", "yes"}

# Failures that never touched the host: re-probing the doctor after them would
# only add latency to the response.
_NO_DOCTOR_REFRESH = frozenset({
    "operation_in_progress", "volume_not_found", "volume_changed",
    "ambiguous_volume", "confirmation_mismatch", "invalid_request",
    "not_mounted",
})


def _form_bool(raw: str | None) -> bool:
    return (raw or "").strip().lower() in _FORM_TRUE


async def _action_body(request: Request) -> ActionRequest:
    """Decode a JSON or browser form-encoded action body.

    htmx 2.x always serializes a form as FormData, which httpx and browsers
    send as application/x-www-form-urlencoded when no files are involved, so
    the endpoint must accept both encodings. Anything else raises ValueError
    and is answered with the JSON error envelope, never a 500.

    The urlencoded branch parses with the stdlib: Starlette gates
    ``request.form()`` behind python-multipart, which this app does not ship.
    """
    content_type = request.headers.get("content-type", "")
    content_type = content_type.split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        return ActionRequest.model_validate(await request.json())
    if content_type == "application/x-www-form-urlencoded":
        raw = (await request.body()).decode("utf-8", "replace")
        form = dict(parse_qsl(raw, keep_blank_values=True))
        size = form.get("size")
        return ActionRequest(
            label=form.get("label", ""),
            size=int(size) if size else None,
            recover=_form_bool(form.get("recover")),
            remove_hiberfile=_form_bool(form.get("remove_hiberfile")),
            confirm=form.get("confirm") or None,
        )
    raise ValueError(f"unsupported content type {content_type!r}")


async def stream_frames(
    bus: EventBus, last_event_id: int, ping_interval: float = 20.0
) -> AsyncIterator[str]:
    queue = bus.subscribe()
    try:
        for frame in bus.replay(last_event_id):
            yield frame
        while True:
            try:
                yield await asyncio.wait_for(queue.get(), ping_interval)
            except asyncio.TimeoutError:
                yield f"event: ping\ndata: {json.dumps({})}\n\n"
    finally:
        bus.unsubscribe(queue)


async def reconcile_once(service, bus: EventBus, last_json: str) -> str:
    if service.busy:
        return last_json
    snap = await asyncio.to_thread(service.snapshot)
    # The snapshot ran off-loop; an action may have taken the lock while it
    # did, so a state taken mid-operation must not be broadcast.
    if service.busy:
        return last_json
    payload = state_model(snap).model_dump(mode="json")
    current = _state_key(payload)
    if current != last_json:
        bus.publish("state", payload)
        return current
    return last_json


def _state_key(payload: dict) -> str:
    stable = {key: value for key, value in payload.items() if key != "generated_at"}
    return json.dumps(stable, sort_keys=True)


async def _state_for(svc: Service) -> StateModel:
    return state_model(await asyncio.to_thread(svc.snapshot))


async def _detail_response(request: Request, svc: Service, selected: str,
                           error=None, *, oob: bool = False):
    return templates.TemplateResponse(
        request, "_detail.html",
        {"state": await _state_for(svc), "selected": selected,
         "activity": svc.activity(), "error": error, "oob": oob},
    )


async def _region_response(request: Request, svc: Service, state: StateModel,
                           selected: str):
    return templates.TemplateResponse(
        request, "_region.html",
        {"state": state, "selected": selected,
         "activity": svc.activity(), "error": None, "oob": False},
    )


def create_app(settings: Settings, *, service: Service | None = None) -> FastAPI:
    svc = service or Service(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        svc.events.bind(asyncio.get_running_loop())
        app.state.last_state_json = ""
        try:
            # The initial doctor is "ok" until a check runs; refresh it before
            # the first request so JSON clients never see a stale pass.
            await asyncio.to_thread(svc.refresh_doctor)
        except Exception:
            logger.exception("startup doctor check failed")

        async def reconcile_loop():
            while True:
                await asyncio.sleep(5)
                if not svc.events.has_subscribers():
                    continue
                try:
                    async with app.state.state_lock:
                        app.state.last_state_json = await reconcile_once(
                            svc, svc.events, app.state.last_state_json)
                except Exception:
                    logger.exception("reconcile tick failed")

        task = asyncio.create_task(reconcile_loop())
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="ntfsmnt web", docs_url=None, redoc_url=None,
                  lifespan=lifespan)
    app.state.last_state_json = ""
    app.state.state_lock = asyncio.Lock()

    @app.middleware("http")
    async def host_guard(request: Request, call_next):
        if request.headers.get("host", "") not in settings.allowed_hosts:
            return _error(421, "forbidden_host", "This server only answers on loopback.")
        return await call_next(request)

    # Declared last so it wraps host_guard: even a rejected host gets headers.
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    @app.get("/", response_class=HTMLResponse)
    async def page(request: Request, token: str | None = None):
        if token is not None:
            if not hmac.compare_digest(token.encode(), settings.token.encode()):
                return HTMLResponse(
                    "<p>ntfsmnt: invalid launch token &mdash; relaunch with "
                    "<code>sudo ntfsmnt web</code>.</p>",
                    status_code=401,
                )
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
        snap = await _state_for(svc)
        return templates.TemplateResponse(
            request, "index.html",
            {"state": snap,
             "selected": snap.volumes[0].id if snap.volumes else None,
             "activity": svc.activity(), "error": None, "oob": False},
        )

    @app.get("/partials/volumes", response_class=HTMLResponse)
    async def partial(request: Request, selected: str | None = None):
        require_session(settings, request)
        snap = await _state_for(svc)
        chosen = selected if selected is not None else (
            snap.volumes[0].id if snap.volumes else "")
        return await _region_response(request, svc, snap, chosen)

    @app.get("/api/v1/state", response_model=StateModel)
    async def state(request: Request):
        require_session(settings, request)
        return state_model(await asyncio.to_thread(svc.snapshot))

    @app.get("/api/v1/activity", response_model=ActivityModel)
    async def activity(request: Request):
        require_session(settings, request)
        return activity_model(svc.activity())

    @app.get("/api/v1/events")
    async def events(request: Request):
        require_session(settings, request)
        header = request.headers.get("last-event-id", "0")
        last = int(header) if header.isdigit() else 0
        # A new connection gets the current state immediately; the reconcile
        # tick only runs while at least one subscriber is attached.
        try:
            async with app.state.state_lock:
                app.state.last_state_json = await reconcile_once(
                    svc, svc.events, app.state.last_state_json)
        except Exception:
            logger.exception("failed to publish initial SSE state")
        return StreamingResponse(
            stream_frames(svc.events, last),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

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
    async def act(volume_id: str, action: str, request: Request):
        try:
            require_mutation(settings, request)
        except HTTPException as exc:
            code = "unauthorized" if exc.status_code == 401 else "forbidden_origin"
            return _error(exc.status_code, code, exc.detail)
        if action not in ACTIONS:
            return _error(404, "not_found", f"Unknown action {action!r}.")
        try:
            body = await _action_body(request)
        except ValueError:
            logger.info("unreadable action body for %s on %s", action, volume_id)
            return _error(422, "invalid_request",
                          "Could not read the action request.",
                          "Send a JSON or form-encoded body.")
        try:
            result = await asyncio.to_thread(ACTIONS[action], volume_id, body)
        except Exception:
            logger.exception("action %s on %s failed", action, volume_id)
            return _error(500, "internal", "Unexpected error; see the server log.")
        if not result.ok and (
            result.error is None or result.error.code not in _NO_DOCTOR_REFRESH
        ):
            # Spec §8: a host-touching failure re-checks the host. Off-loop, and
            # never fatal to the response the user is waiting for.
            try:
                await asyncio.to_thread(svc.refresh_doctor)
            except Exception:
                logger.exception("doctor check after failed %s failed", action)
        try:
            app.state.last_state_json = await _publish_state(svc, request.app)
        except Exception:
            logger.exception("failed to publish state after %s on %s", action, volume_id)
        if request.headers.get("hx-request"):
            # Always 200 for htmx so the fragment swaps and any error renders
            # inline; JSON clients still receive the real status below.
            return await _detail_response(
                request, svc,
                result.volume.id if result.volume else volume_id,
                error=result.error, oob=not result.ok,
            )
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


async def _publish_state(svc: Service, app: FastAPI) -> str:
    async with app.state.state_lock:
        snap = await asyncio.to_thread(svc.snapshot)
        payload = state_model(snap).model_dump(mode="json")
        svc.events.publish("state", payload)
        app.state.last_state_json = _state_key(payload)
        return app.state.last_state_json


def run_server(settings: Settings) -> None:
    import socket
    import uvicorn
    from ntfsmnt.web.config import with_port

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((settings.host, settings.port))
        port = sock.getsockname()[1]
        settings = with_port(settings, port)
        url = f"http://{settings.host}:{port}/?token={settings.token}"
        print(f"ntfsmnt web: {url}", flush=True)
        if settings.open_browser:
            import webbrowser
            webbrowser.open(url)
        config = uvicorn.Config(
            create_app(settings), log_level="warning", access_log=False,
        )
        try:
            uvicorn.Server(config).run(sockets=[sock])
        except KeyboardInterrupt:
            # uvicorn re-raises the captured SIGINT after a graceful shutdown;
            # the Ctrl-C that stops the service is not an error.
            pass
    finally:
        sock.close()
