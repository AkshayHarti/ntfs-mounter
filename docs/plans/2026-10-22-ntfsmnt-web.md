# ntfsmnt web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A loopback FastAPI + htmx page that shows every NTFS volume, triggers the existing CLI operations, and guides recovery — without weakening a single CLI safety invariant.

**Architecture:** `ntfsmnt/web/` is an optional subpackage. A `Service` owns discovery, the single operation lock, action orchestration, and an in-memory activity ring; it calls the same `mounter` / `unmounter` / `doctor` functions the CLI calls, with the same injected runners. FastAPI is a thin transport: auth middleware, JSON endpoints, one SSE stream, and Jinja templates. The CLI stays stdlib-only; `ntfsmnt web` imports the subpackage lazily.

**Tech Stack:** Python ≥3.12, FastAPI, uvicorn, Jinja2, htmx 2 (vendored or CDN-pinned, no build step), pytest, httpx (TestClient).

**Spec:** `docs/specs/2026-10-22-ntfsmnt-web-design.md`

## Global Constraints

- CLI runtime stays stdlib-only. FastAPI/uvicorn/jinja2 live only in `[project.optional-dependencies] web`.
- `uv run pytest -q` must stay green without the web extra. Every web test starts with `pytest.importorskip(...)` except the three stdlib-only suites: `tests/test_web_config.py`, `tests/test_web_models_events.py`, and `tests/test_web_service.py`.
- Bind `127.0.0.1` only; never expose `--host`, TLS, or multiple workers.
- Token: `secrets.token_urlsafe(32)`, regenerated per launch, exchanged for cookie `ntfsmnt_session` = `sha256(token)` hex, compared with `hmac.compare_digest`.
- Mutations require cookie + header `X-NTFSMNT: 1` + `Origin` exactly in `allowed_origins`; `Host` must be in `allowed_hosts` (else 421).
- Volume identity is `uuid` or `dev-<identifier>`; actions re-discover and require UUID **and** label to match, else `volume_changed`.
- `remove_hiberfile` requires `confirm == label` server-side before the mounter is called. `recover` is opt-in without a typed label.
- Never call `ntfsfix`, `ntfsresize`, `mkntfs`, `ntfswipe`, `osascript`, or `SIGKILL`. Rollback stays in `mounter.py`.
- All volume-derived strings (label, UUID, mount point, stderr) render through Jinja autoescape only — no `|safe`, no `innerHTML`.
- Ring sizes and timings: activity 100 entries, SSE replay 100, reconcile 5 s (paused while the lock is held), SSE ping 20 s.
- Responsive breakpoint 900 px. State is never colour-only. Focus ring 2 px.
- Exact copy: `sudo ntfsmnt web`, `X-NTFSMNT`, `ntfsmnt_session`, `/api/v1/state`, `/api/v1/events`, `/api/v1/activity`, `/partials/volumes`.

## Review Focus

1. **Hostile volume label or stderr** — a USB stick named `<img src=x onerror=...>` must render as text everywhere, including the activity log. Owning test: Task 9.
2. **Drive swapped while the page is open** — the old id now names a different volume; the action must fail `volume_changed`, never mount the new drive. Owning test: Task 4.
3. **Root without `SUDO_UID`/`SUDO_GID`** — must be read-only, never mount root-owned files. Owning tests: Tasks 1, 3.
4. **Double-click / two tabs** — second action gets `409 operation_in_progress`, the first completes untouched. Owning tests: Tasks 4, 7.
5. **Volume with an empty label** — the row must render and show why mount is disabled, not crash or offer a doomed action. Owning test: Task 3.

---

## File Structure

```
ntfsmnt/web/__init__.py        empty; must not import FastAPI
ntfsmnt/web/config.py          Settings, owner/token/host derivation (stdlib only)
ntfsmnt/web/models.py          plain frozen dataclasses shared by service/events/schemas
ntfsmnt/web/events.py          ActivityLog (100-entry ring), EventBus (SSE fan-out + replay)
ntfsmnt/web/service.py         Service: discovery, snapshot, lock, mount/unmount/eject
ntfsmnt/web/schemas.py         pydantic response models + builders
ntfsmnt/web/app.py             create_app(), auth middleware, routes, SSE, reconcile, run_server()
ntfsmnt/web/page/index.html    page shell, header, doctor banner, live regions
ntfsmnt/web/page/_region.html  wrapper for list + detail (htmx refresh target)
ntfsmnt/web/page/_list.html    volume list (buttons + chips)
ntfsmnt/web/page/_detail.html  detail panel, actions, inline confirm, activity
ntfsmnt/web/page/_activity.html  activity rows
ntfsmnt/web/page/app.css       editorial-light tokens and layout
ntfsmnt/web/page/app.js        SSE client, confirm UX, focus/keyboard, reconnect
ntfsmnt/web/page/htmx.min.js   vendored htmx 2.0.4 (no CDN at runtime)
tests/web_helpers.py           shared Device/Volume/Settings builders
tests/web_render.py            test-only Jinja environment + render helpers
tests/test_web_config.py
tests/test_web_models_events.py
tests/test_web_service.py
tests/test_web_schemas.py
tests/test_web_api.py
tests/test_web_sse.py
tests/test_web_templates.py
tests/test_web_cli.py
```

Jinja templates and CSS live inside the package, so hatchling ships them with the wheel; no extra build step.

---

### Task 1: Settings, packaging, optional extra

**Files:**
- Create: `ntfsmnt/web/__init__.py` (empty)
- Create: `ntfsmnt/web/config.py`
- Create: `ntfsmnt/web/page/.gitkeep` (placeholder so `StaticFiles` can mount before Task 9)
- Create: `tests/test_web_config.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing.
- Produces: `Settings`, `settings_from(*, port, owner, environ, euid, token=None, open_browser=False) -> Settings`, `with_port(settings: Settings, port: int) -> Settings`, `cookie_value(token: str) -> str`, `cookie_matches(token: str, presented: str | None) -> bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_config.py
import pytest

from ntfsmnt.web.config import cookie_matches, cookie_value, settings_from, with_port


def make(**kw):
    base = dict(port=0, owner=None, environ={}, euid=0, token="tok")
    base.update(kw)
    return settings_from(**base)


def test_root_with_sudo_owner_can_write():
    s = make(environ={"SUDO_UID": "501", "SUDO_GID": "20"})
    assert s.owner == (501, 20)
    assert s.read_only is False


def test_root_without_owner_is_read_only():
    s = make()
    assert s.owner is None
    assert s.read_only is True


def test_non_root_is_read_only_even_with_owner():
    s = make(environ={"SUDO_UID": "501", "SUDO_GID": "20"}, euid=501)
    assert s.read_only is True


def test_explicit_owner_wins_over_environment():
    s = make(owner="502:99", environ={"SUDO_UID": "501", "SUDO_GID": "20"})
    assert s.owner == (502, 99)


@pytest.mark.parametrize("bad", ["501", "501:", ":20", "a:b", "1:2:3"])
def test_bad_owner_is_rejected(bad):
    with pytest.raises(ValueError):
        make(owner=bad)


def test_with_port_fixes_loopback_hosts_and_origins():
    s = with_port(make(), 8765)
    assert s.port == 8765
    assert s.allowed_hosts == frozenset({"127.0.0.1:8765", "localhost:8765"})
    assert s.allowed_origins == frozenset(
        {"http://127.0.0.1:8765", "http://localhost:8765"}
    )


def test_tokens_are_random_when_not_supplied():
    a, b = make(token=None), make(token=None)
    assert a.token != b.token
    assert len(a.token) >= 43


def test_cookie_matches_only_its_token():
    cookie = cookie_value("tok")
    assert cookie != "tok"
    assert cookie_matches("tok", cookie)
    assert not cookie_matches("other", cookie)
    assert not cookie_matches("tok", None)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_config.py -q`
Expected: `ModuleNotFoundError: No module named 'ntfsmnt.web'`.

- [ ] **Step 3: Implement `config.py`**

```python
# ntfsmnt/web/config.py
import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Settings:
    token: str
    host: str = "127.0.0.1"
    port: int = 0
    owner: tuple[int, int] | None = None
    root: bool = False
    read_only: bool = True
    open_browser: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    allowed_origins: frozenset[str] = frozenset()
    cookie_name: str = "ntfsmnt_session"


def _parse_owner(text: str) -> tuple[int, int]:
    uid, sep, gid = text.partition(":")
    if not sep:
        raise ValueError(f"owner must be uid:gid, got {text!r}")
    try:
        return int(uid), int(gid)
    except ValueError as exc:
        raise ValueError(f"owner must be uid:gid, got {text!r}") from exc


def _env_owner(environ: Mapping[str, str]) -> tuple[int, int] | None:
    try:
        return int(environ["SUDO_UID"]), int(environ["SUDO_GID"])
    except (KeyError, ValueError):
        return None


def settings_from(
    *,
    port: int,
    owner: str | None,
    environ: Mapping[str, str],
    euid: int,
    token: str | None = None,
    open_browser: bool = False,
) -> Settings:
    resolved = _parse_owner(owner) if owner is not None else _env_owner(environ)
    root = euid == 0
    return Settings(
        token=token or secrets.token_urlsafe(32),
        port=port,
        owner=resolved,
        root=root,
        read_only=(not root) or resolved is None,
        open_browser=open_browser,
    )


def with_port(settings: Settings, port: int) -> Settings:
    return replace(
        settings,
        port=port,
        allowed_hosts=frozenset({f"127.0.0.1:{port}", f"localhost:{port}"}),
        allowed_origins=frozenset(
            {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        ),
    )


def cookie_value(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_matches(token: str, presented: str | None) -> bool:
    return presented is not None and hmac.compare_digest(
        cookie_value(token), presented
    )
```

- [ ] **Step 4: Add the optional extra and dev dependency**

```toml
# pyproject.toml — add after [project.scripts]
[project.optional-dependencies]
web = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1"]

# replace the existing dependency-groups block
[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]
```

Run: `uv sync --extra web` — expected: resolves and installs.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_config.py -q`
Expected: 12 passed (4 plain + 5 parametrized + 3).

Run: `uv run pytest -q`
Expected: 153 existing tests still pass (plus the new 12).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock ntfsmnt/web/__init__.py ntfsmnt/web/config.py ntfsmnt/web/page/.gitkeep tests/test_web_config.py
git commit -m "feat(web): optional extra and session settings"
```

---

### Task 2: Shared models, activity ring, SSE bus

**Files:**
- Create: `ntfsmnt/web/models.py`
- Create: `ntfsmnt/web/events.py`
- Create: `tests/test_web_models_events.py`

**Interfaces:**
- Consumes: `Settings` from Task 1.
- Produces:
  - dataclasses `Finding`, `DoctorStatus`, `OwnerView`, `HostView`, `OperationView`, `ActionView`, `OptionView`, `VolumeView`, `Snapshot`, `ActivityEntry`, `ActionError`, `ActionResult`.
  - `ActivityLog(limit=100)` with `.append(action, volume, ok, summary, detail=None) -> ActivityEntry` and `.entries() -> tuple[ActivityEntry, ...]` (newest first).
  - `EventBus()` with `.publish(event: str, data) -> int`, `.subscribe() -> asyncio.Queue[str]`, `.unsubscribe(q)`, `.replay(last_id: int) -> list[str]`, `.has_subscribers() -> bool`, `.bind(loop)`.
  - `format_frame(event_id: int, event: str, data) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_models_events.py
import asyncio
import json

from ntfsmnt.web.events import ActivityLog, EventBus
from ntfsmnt.web.models import ActionError, ActionResult


def test_activity_is_newest_first_and_capped():
    log = ActivityLog(limit=2)
    log.append("mount", "HD1", True, "writable")
    log.append("unmount", "HD2", True, "read-only")
    third = log.append("eject", "HD1", False, "busy", "still mounted")
    assert [e.id for e in log.entries()] == [third.id, third.id - 1]
    assert log.entries()[0].detail == "still mounted"


def test_bus_publish_increments_and_formats_sse():
    bus = EventBus()
    assert bus.publish("state", {"a": 1}) == 1
    assert bus.publish("ping", {}) == 2
    assert bus.replay(0) == [
        'id: 1\nevent: state\ndata: {"a": 1}\n\n',
        "id: 2\nevent: ping\ndata: {}\n\n",
    ]


def test_bus_replay_starts_after_last_id():
    bus = EventBus()
    bus.publish("a", {})
    bus.publish("b", {})
    assert len(bus.replay(1)) == 1
    assert "event: b" in bus.replay(1)[0]


def test_bus_ring_keeps_last_hundred():
    bus = EventBus()
    for i in range(105):
        bus.publish("state", {"i": i})
    assert len(bus.replay(0)) == 100
    assert "id: 105" in bus.replay(0)[-1]


def test_subscriber_receives_published_frame():
    async def go():
        bus = EventBus()
        queue = bus.subscribe()
        bus.publish("state", {"ok": True})
        frame = await asyncio.wait_for(queue.get(), 1)
        assert json.loads(frame.split("data: ", 1)[1]) == {"ok": True}
        assert bus.has_subscribers() is True
        bus.unsubscribe(queue)
        assert bus.has_subscribers() is False

    asyncio.run(go())


def test_action_error_and_result_defaults():
    error = ActionError(code="busy", message="HD1 is in use.", status=409)
    assert error.next_step is None and error.volume is None
    result = ActionResult(ok=False, message="HD1 is in use.", restored=None,
                          volume=None, error=error)
    assert result.error.code == "busy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_models_events.py -q`
Expected: import error for `ntfsmnt.web.events`.

- [ ] **Step 3: Implement `models.py`**

```python
# ntfsmnt/web/models.py
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str
    fix: str | None = None


@dataclass(frozen=True)
class DoctorStatus:
    status: str
    checked_at: str
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class OwnerView:
    uid: int
    gid: int


@dataclass(frozen=True)
class HostView:
    root: bool
    read_only: bool
    owner: OwnerView | None
    fuse_t: bool
    ntfs_3g: bool


@dataclass(frozen=True)
class OperationView:
    id: str
    action: str
    volume: str
    started_at: str


@dataclass(frozen=True)
class ActionView:
    name: str
    enabled: bool
    reason: str | None = None


@dataclass(frozen=True)
class OptionView:
    name: str
    enabled: bool
    guarded: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class VolumeView:
    id: str
    device: str
    label: str
    uuid: str
    size: int
    fs: str
    state: str
    mount_point: str | None
    driver: str | None
    pid: int | None
    flags: dict[str, bool] = field(default_factory=dict)
    actions: tuple[ActionView, ...] = ()
    options: tuple[OptionView, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    generated_at: str
    host: HostView
    doctor: DoctorStatus
    operation: OperationView | None
    volumes: tuple[VolumeView, ...]


@dataclass(frozen=True)
class ActivityEntry:
    id: int
    at: str
    action: str
    volume: str
    ok: bool
    summary: str
    detail: str | None = None


@dataclass(frozen=True)
class ActionError:
    code: str
    message: str
    status: int
    next_step: str | None = None
    detail: str | None = None
    volume: str | None = None


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    restored: bool | None
    volume: VolumeView | None
    error: ActionError | None = None
```

- [ ] **Step 4: Implement `events.py`**

```python
# ntfsmnt/web/events.py
import asyncio
import json
import threading
from collections import deque
from typing import Any

from ntfsmnt.web.models import ActivityEntry


def format_frame(event_id: int, event: str, data: Any) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data)}\n\n"


class ActivityLog:
    def __init__(self, limit: int = 100) -> None:
        self._items: deque[ActivityEntry] = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._next = 1

    def append(self, action, volume, ok, summary, detail=None) -> ActivityEntry:
        with self._lock:
            entry = ActivityEntry(
                id=self._next, at=_now_iso(), action=action, volume=volume,
                ok=ok, summary=summary, detail=detail,
            )
            self._next += 1
            self._items.appendleft(entry)
            return entry

    def entries(self) -> tuple[ActivityEntry, ...]:
        with self._lock:
            return tuple(self._items)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EventBus:
    def __init__(self, limit: int = 100) -> None:
        self._subs: set[asyncio.Queue[str]] = set()
        self._history: deque[str] = deque(maxlen=limit)
        self._next = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: str, data: Any) -> int:
        self._next += 1
        frame = format_frame(self._next, event, data)
        self._history.append(frame)
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._fanout, frame)
        else:
            self._fanout(frame)
        return self._next

    def _fanout(self, frame: str) -> None:
        for queue in list(self._subs):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(frame)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subs.discard(queue)

    def replay(self, last_id: int) -> list[str]:
        if last_id <= 0:
            return list(self._history)
        return [f for f in self._history if _frame_id(f) > last_id]

    def has_subscribers(self) -> bool:
        return bool(self._subs)


def _frame_id(frame: str) -> int:
    return int(frame.split(":", 1)[1].split("\n", 1)[0])
```

`ActivityLog.append` and `_now_iso` are called from worker threads; the lock covers the deque. `EventBus.publish` is called from a worker thread and hops to the loop when bound.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_models_events.py -q`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add ntfsmnt/web/models.py ntfsmnt/web/events.py tests/test_web_models_events.py
git commit -m "feat(web): shared models, activity ring, SSE bus"
```

---

### Task 3: Service snapshot and doctor

**Files:**
- Create: `ntfsmnt/web/service.py`
- Create: `tests/web_helpers.py`
- Create: `tests/test_web_service.py`

**Interfaces:**
- Consumes: `Settings`, `models`, `events.ActivityLog`, `events.EventBus`, `doctor.run_checks`/`hard_failures`/`Env`, `cli.real_run`, `diskutil.read_all_infos`, `mounttable.read_mount_table`, `volumes.build_volumes`.
- Produces:
  - `volume_id(volume: Volume) -> str`
  - `default_discover() -> list[Volume]`
  - `Service(settings, *, discover=default_discover, run=real_run, mounts=read_mount_table, sleep=time.sleep, events=None, activity=None, doctor_env=None, clock=utcnow)`
  - `.snapshot() -> Snapshot`, `.refresh_doctor() -> DoctorStatus`, `.activity() -> tuple[ActivityEntry, ...]`, `.busy -> bool`, `.operation(...)` context manager (Task 4 fills actions).

- [ ] **Step 1: Write the shared test helpers**

```python
# tests/web_helpers.py
from ntfsmnt.models import Device
from ntfsmnt.volumes import Volume
from ntfsmnt.web.config import Settings


def make_device(identifier="disk4s1", *, label="HD1", uuid="UUID-1",
                size=1_000_000_000, mount_point=None):
    return Device(
        identifier=identifier, device=f"/dev/{identifier}", label=label,
        uuid=uuid, size=size, fs_type="Windows_NTFS", mount_point=mount_point,
    )


def make_volume(*, mounts=(), **kw):
    return Volume(device=make_device(**kw), mounts=tuple(mounts))


def make_settings(*, token="tok", root=True, owner=(501, 20), read_only=False,
                  allowed_hosts=frozenset({"testserver"}),
                  allowed_origins=frozenset({"http://testserver"})):
    return Settings(
        token=token, port=0, owner=owner, root=root, read_only=read_only,
        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
    )


class FakeRun:
    def __init__(self, pid="4242"):
        self.pid = pid
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == "pgrep":
            return (0, f"{self.pid}\n") if self.pid else (1, "")
        raise AssertionError(f"unexpected command: {argv}")
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_web_service.py
import pytest

from ntfsmnt.doctor import Check, Env
from ntfsmnt.mounttable import MountEntry
from ntfsmnt.web import service as service_module
from ntfsmnt.web.models import DoctorStatus, Finding
from ntfsmnt.web.service import Service, volume_id
from tests.web_helpers import FakeRun, make_settings, make_volume

RW = MountEntry(source="fuse-t:/HD1", mount_point="/Volumes/HD1", fs="nfs", read_only=False)
RO = MountEntry(source="/dev/disk4s1", mount_point="/Volumes/HD1", fs="ntfs", read_only=True)


def make_service(volumes, *, doctor=None, run=None, read_only=False):
    settings = make_settings(read_only=read_only)
    svc = Service(
        settings,
        discover=lambda: list(volumes),
        run=run or FakeRun(),
        mounts=lambda: [],
        doctor_env=Env(
            path_exists=lambda p: True, run=lambda argv: (0, ""), euid=0,
            linkage=lambda p: "libfuse-t", first_device=lambda: None,
        ),
    )
    if doctor is not None:
        svc._doctor = doctor
    return svc


def test_volume_id_prefers_uuid():
    assert volume_id(make_volume(uuid="UUID-9")) == "UUID-9"


def test_volume_id_falls_back_to_identifier():
    assert volume_id(make_volume(uuid="")) == "dev-disk4s1"


def test_read_only_volume_offers_mount():
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    view = svc.snapshot().volumes[0]
    assert view.state == "ro"
    assert {a.name: a.enabled for a in view.actions} == {
        "mount": True, "unmount": False, "eject": False,
    }


def test_rw_volume_offers_unmount_and_reports_pid():
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    svc = make_service([rw], run=FakeRun(pid="4242"))
    view = svc.snapshot().volumes[0]
    assert view.state == "rw"
    assert view.driver == "fuse-t"
    assert view.pid == 4242
    assert {a.name: a.enabled for a in view.actions}["unmount"] is True


def test_unmounted_volume_offers_mount_and_eject():
    svc = make_service([make_volume()])
    view = svc.snapshot().volumes[0]
    assert view.state == "unmounted"
    assert {a.name: a.enabled for a in view.actions} == {
        "mount": True, "unmount": False, "eject": True,
    }


def test_remove_hiberfile_is_guarded():
    svc = make_service([make_volume()])
    option = next(o for o in svc.snapshot().volumes[0].options
                  if o.name == "remove_hiberfile")
    assert option.guarded is True
    assert "hibernation" in (option.reason or "").lower()


def test_read_only_mode_disables_every_action():
    svc = make_service([make_volume()], read_only=True)
    view = svc.snapshot().volumes[0]
    assert view.actions and all(not a.enabled for a in view.actions)
    assert all("sudo ntfsmnt web" in (a.reason or "") for a in view.actions)


def test_empty_label_disables_mount():
    svc = make_service([make_volume(label="")])
    mount = next(a for a in svc.snapshot().volumes[0].actions if a.name == "mount")
    assert mount.enabled is False
    assert "label" in (mount.reason or "").lower()


def test_doctor_blocker_disables_actions():
    doc = DoctorStatus(
        "blocker", "now",
        (Finding("blocker", "fuse-t", "libfuse-t missing", "install it"),),
    )
    svc = make_service([make_volume()], doctor=doc)
    view = svc.snapshot().volumes[0]
    assert all(not a.enabled for a in view.actions)
    assert any("fuse-t" in (a.reason or "") for a in view.actions)


def test_doctor_findings_map_to_severity(monkeypatch):
    def fake_checks(env):
        return [
            Check("macos", True, "macOS 26"),
            Check("fuse-t", False, "missing", "brew install --cask fuse-t"),
            Check("root", True, "root"),
        ]

    monkeypatch.setattr(service_module, "run_checks", fake_checks)
    svc = make_service([make_volume()])
    doc = svc.refresh_doctor()
    assert doc.status == "blocker"
    assert [(f.check, f.severity) for f in doc.findings] == [("fuse-t", "blocker")]
    assert svc.snapshot().host.fuse_t is False
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_service.py -q`
Expected: import error for `ntfsmnt.web.service`.

- [ ] **Step 4: Implement `service.py` (snapshot half)**

```python
# ntfsmnt/web/service.py
from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
from threading import Lock

from ntfsmnt.cli import real_run
from ntfsmnt.diskutil import read_all_infos
from ntfsmnt.doctor import HARD_CHECK_NAMES, Check, Env, run_checks
from ntfsmnt.identity import parse_pid, pid_pattern
from ntfsmnt.mounttable import MountEntry, read_mount_table
from ntfsmnt.volumes import Volume, build_volumes
from ntfsmnt.web.config import Settings
from ntfsmnt.web.events import ActivityLog, EventBus
from ntfsmnt.web.models import (
    ActionView, DoctorStatus, Finding, HostView, OperationView, OptionView,
    OwnerView, Snapshot, VolumeView,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def volume_id(volume: Volume) -> str:
    return volume.device.uuid or f"dev-{volume.device.identifier}"


def default_discover() -> list[Volume]:
    return build_volumes(read_all_infos(), read_mount_table())


class Service:
    def __init__(
        self,
        settings: Settings,
        *,
        discover: Callable[[], list[Volume]] = default_discover,
        run: Callable[[list[str]], tuple[int, str]] = real_run,
        mounts: Callable[[], list[MountEntry]] = read_mount_table,
        sleep: Callable[[float], None] = time.sleep,
        events: EventBus | None = None,
        activity: ActivityLog | None = None,
        doctor_env: Env | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self._discover = discover
        self._run = run
        self._mounts = mounts
        self._sleep = sleep
        self.events = events or EventBus()
        self.activity_log = activity or ActivityLog()
        self._doctor = DoctorStatus(status="ok", checked_at="", findings=())
        self._doctor_env = doctor_env
        self._clock = clock
        self._lock = Lock()
        self._operation: OperationView | None = None
        self._op_ids = count(1)
        self._flags: dict[str, dict[str, bool]] = {}

    @property
    def busy(self) -> bool:
        return self._operation is not None

    def activity(self):
        return self.activity_log.entries()

    def refresh_doctor(self) -> DoctorStatus:
        env = self._doctor_env or self._default_doctor_env()
        checks = run_checks(env)
        findings = tuple(
            Finding(
                severity="blocker" if c.name in HARD_CHECK_NAMES else "warning",
                check=c.name, message=c.detail, fix=c.fix,
            )
            for c in checks if not c.ok
        )
        status = (
            "blocker" if any(f.severity == "blocker" for f in findings)
            else "warning" if findings else "ok"
        )
        self._doctor = DoctorStatus(status=status, checked_at=iso(self._clock()), findings=findings)
        return self._doctor

    def _default_doctor_env(self) -> Env:
        from ntfsmnt.cli import _doctor_env
        return _doctor_env()

    def snapshot(self) -> Snapshot:
        volumes = self._discover()
        views = tuple(self._view(v) for v in volumes)
        return Snapshot(
            generated_at=iso(self._clock()),
            host=self._host(),
            doctor=self._doctor,
            operation=self._operation,
            volumes=views,
        )

    def _host(self) -> HostView:
        failed = {f.check for f in self._doctor.findings}
        return HostView(
            root=self.settings.root,
            read_only=self.settings.read_only,
            owner=OwnerView(*self.settings.owner) if self.settings.owner else None,
            fuse_t="fuse-t" not in failed,
            ntfs_3g="ntfs-3g" not in failed,
        )

    def _view(self, volume: Volume) -> VolumeView:
        identifier = volume_id(volume)
        driver = {"rw": "fuse-t", "ro": "fskit"}.get(volume.state)
        pid = (
            parse_pid(self._run(["pgrep", "-f", pid_pattern(volume.device.device)])[1])
            if volume.state == "rw" else None
        )
        return VolumeView(
            id=identifier,
            device=volume.device.device,
            label=volume.device.label,
            uuid=volume.device.uuid,
            size=int(volume.device.size),
            fs="ntfs",
            state=volume.state,
            mount_point=volume.mount_point,
            driver=driver,
            pid=pid,
            flags=dict(self._flags.get(identifier, {"hibernated": False, "dirty": False})),
            actions=self._actions(volume),
            options=self._options(volume),
        )

    def _doctor_blocker(self):
        return next(
            (f for f in self._doctor.findings
             if f.severity == "blocker"
             and f.check in {"fuse-t", "ntfs-3g", "device-access"}),
            None,
        )

    def _reason(self) -> str | None:
        if self.settings.read_only:
            return "Read-only mode: restart with `sudo ntfsmnt web` to enable actions."
        blocker = self._doctor_blocker()
        if blocker is not None:
            fix = f" {blocker.fix}" if blocker.fix else ""
            return f"Host check failed: {blocker.message}.{fix}".strip()
        return None

    def _actions(self, volume: Volume) -> tuple[ActionView, ...]:
        mode = self._reason()
        label_missing = not volume.device.label
        return (
            ActionView("mount", mode is None and not label_missing and volume.state != "rw",
                       mode or ("volume has no label; relabel it before mounting" if label_missing
                                else "already writable" if volume.state == "rw" else None)),
            ActionView("unmount", mode is None and volume.state == "rw",
                       mode or ("not mounted read-write" if volume.state != "rw" else None)),
            ActionView("eject", mode is None and volume.state == "unmounted",
                       mode or ("unmount it first" if volume.state != "unmounted" else None)),
        )

    def _options(self, volume: Volume) -> tuple[OptionView, ...]:
        mode = self._reason()
        available = mode is None and volume.state != "rw"
        return (
            OptionView("recover", available, False, mode),
            OptionView("remove_hiberfile", available, True,
                       mode or "Windows hibernation state will be discarded."),
        )
```

`_default_doctor_env` imports `_doctor_env` lazily to avoid a module-level circular import.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_service.py -q`
Expected: 10 passed.

- [ ] **Step 6: Commit**

```bash
git add ntfsmnt/web/service.py tests/web_helpers.py tests/test_web_service.py
git commit -m "feat(web): volume snapshot and doctor status"
```

---

### Task 4: Action orchestration

**Files:**
- Modify: `ntfsmnt/web/service.py`
- Modify: `tests/test_web_service.py`

**Interfaces:**
- Consumes: everything from Task 3; `mounter.mount_volume`, `MountFailed`; `unmounter.unmount_volume`, `eject_volume`, `UnmountFailed`.
- Produces:
  - `Service.mount(volume_id, *, label, recover=False, remove_hiberfile=False, confirm=None) -> ActionResult`
  - `Service.unmount(volume_id, *, label) -> ActionResult`
  - `Service.eject(volume_id, *, label) -> ActionResult`
  - `Service.operation(action, volume) -> Iterator[OperationView]` (non-blocking; raises `OperationInProgress`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_web_service.py`:

```python
from ntfsmnt.web.service import OperationInProgress
import ntfsmnt.web.service as svc_mod


def test_mount_success_uses_owner_and_flags(monkeypatch):
    calls = {}

    def fake_mount(volume, **kw):
        calls.update(kw)
        from ntfsmnt.mounter import MountResult
        return MountResult(volume=volume, mount_point="/Volumes/HD1", pid=4242)

    monkeypatch.setattr(svc_mod, "mount_volume", fake_mount)
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD1", recover=True)
    assert result.ok is True
    assert calls["uid"] == 501 and calls["gid"] == 20
    assert calls["recover"] is True and calls["remove_hiberfile"] is False
    assert calls["label_shared"] is False


def test_mount_rejects_wrong_label_without_calling_mounter(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD2")
    assert result.error.code == "volume_changed"
    assert result.error.status == 409


def test_mount_rejects_wrong_size_without_calling_mounter(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume(size=1_000_000_000)])
    result = svc.mount("UUID-1", label="HD1", size=2_000_000_000)
    assert result.error.code == "volume_changed"


def test_mount_rejects_unknown_identity():
    svc = make_service([make_volume()])
    result = svc.mount("UUID-404", label="HD1")
    assert result.error.code == "volume_not_found"
    assert result.error.status == 404


def test_duplicate_uuid_is_ambiguous():
    svc = make_service([make_volume(), make_volume(identifier="disk5s1")])
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "ambiguous_volume"
    assert result.error.status == 409


def test_action_in_read_only_mode_is_forbidden():
    svc = make_service([make_volume()], read_only=True)
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code in {"not_root", "owner_unresolved"}
    assert result.error.status == 403


def test_action_with_doctor_blocker_is_409():
    doc = DoctorStatus(
        "blocker", "now",
        (Finding("blocker", "ntfs-3g", "ntfs-3g missing", "install it"),),
    )
    svc = make_service([make_volume()], doctor=doc)
    result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "doctor_blocker"
    assert result.error.status == 409


def test_guarded_mount_requires_exact_label(monkeypatch):
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    svc = make_service([make_volume()])
    for confirm in (None, "HD", "hd1"):
        result = svc.mount("UUID-1", label="HD1", remove_hiberfile=True, confirm=confirm)
        assert result.error.code == "confirmation_mismatch"
        assert result.error.status == 400


def test_hibernated_failure_offers_guarded_mount(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed(
            "The volume is hibernated. Re-run with --remove-hiberfile. "
            "Apple's read-only mount was restored."
        )

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    svc = make_service([make_volume()])
    result = svc.mount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.restored is True
    assert result.error.code == "blocked_hibernated"
    assert result.error.status == 422
    assert result.error.next_step


def test_restore_attempt_is_not_reported_as_restored(monkeypatch):
    def fail(*a, **k):
        from ntfsmnt.mounter import MountFailed
        raise MountFailed("The device vanished. Read-only restore attempted.")

    monkeypatch.setattr(svc_mod, "mount_volume", fail)
    result = make_service([make_volume()]).mount("UUID-1", label="HD1")
    assert result.restored is False


def test_second_action_while_locked_is_refused(monkeypatch):
    svc = make_service([make_volume()])
    with svc.operation("mount", "HD1"):
        result = svc.mount("UUID-1", label="HD1")
    assert result.error.code == "operation_in_progress"
    assert result.error.status == 409


def test_mount_activity_is_recorded(monkeypatch):
    from ntfsmnt.mounter import MountResult
    monkeypatch.setattr(svc_mod, "mount_volume",
                        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 1))
    svc = make_service([make_volume()])
    svc.mount("UUID-1", label="HD1")
    entry = svc.activity()[0]
    assert (entry.action, entry.volume, entry.ok) == ("mount", "HD1", True)


def test_unmount_of_unmounted_volume_is_not_mounted():
    svc = make_service([make_volume()])
    result = svc.unmount("UUID-1", label="HD1")
    assert result.error.code == "not_mounted"


def test_eject_of_mounted_volume_is_busy():
    svc = make_service([make_volume(mounts=[RO], mount_point="/Volumes/HD1")])
    result = svc.eject("UUID-1", label="HD1")
    assert result.error.code == "busy"


def test_unmount_reports_restore_failure(monkeypatch):
    monkeypatch.setattr(svc_mod, "unmount_volume", lambda *a, **k: False)
    rw = make_volume(mounts=[RW], mount_point="/Volumes/HD1")
    result = make_service([rw]).unmount("UUID-1", label="HD1")
    assert result.ok is False
    assert result.restored is False
    assert result.error.code == "action_failed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_service.py -q`
Expected: `AttributeError: 'Service' object has no attribute 'mount'`.

- [ ] **Step 3: Implement the action half of `service.py`**

Add imports:

```python
from ntfsmnt.mounter import MountFailed, MountResult, mount_volume
from ntfsmnt.unmounter import UnmountFailed, eject_volume, unmount_volume
from ntfsmnt.web.models import ActionError, ActionResult
```

Add the exception at module level and these methods inside `class Service`:

```python
class OperationInProgress(Exception):
    pass


# --- inside class Service ---

@contextmanager
def operation(self, action: str, volume: str) -> Iterator[OperationView]:
    if not self._lock.acquire(blocking=False):
        raise OperationInProgress
    try:
        op = OperationView(
            id=f"op-{next(self._op_ids)}", action=action, volume=volume,
            started_at=iso(self._clock()),
        )
        self._operation = op
        self.events.publish("operation", asdict(op))
        yield op
    finally:
        self._operation = None
        self.events.publish("operation", None)
        self._lock.release()


def _resolve(self, target: str, label: str, size: int | None = None) -> tuple[Volume, list[Volume]]:
    volumes = self._discover()
    matches = [v for v in volumes if volume_id(v) == target]
    if not matches:
        raise _Fail(ActionError("volume_not_found", f"No volume with id {target}.",
                                404, "The drive may have been replugged; rescan."))
    if len(matches) > 1:
        raise _Fail(ActionError("ambiguous_volume",
                                "More than one volume has this identity.",
                                409, "Replug the drive or rescan."))
    volume = matches[0]
    if volume.device.label != label or (
        size is not None and int(volume.device.size) != size
    ):
        raise _Fail(ActionError(
            "volume_changed",
            f"The drive at that slot is now {volume.device.label!r}, not {label!r}.",
            409, "Rescan and choose the drive again."))
    return volume, volumes
```

Use a private `_Fail(Exception)` carrying `ActionError` so `_resolve` can raise and the public methods convert it. Add:

```python
class _Fail(Exception):
    def __init__(self, error: ActionError) -> None:
        self.error = error
```

Public methods:

```python
def _guard(self) -> ActionError | None:
    if self.settings.read_only:
        code = "owner_unresolved" if self.settings.root else "not_root"
        detail = (
            "This process is root but SUDO_UID/SUDO_GID were not captured."
            if self.settings.root
            else "The service is not running as root."
        )
        return ActionError(
            code, "Actions are disabled in read-only mode.", 403,
            "Restart with `sudo ntfsmnt web`.", detail=detail,
        )
    blocker = self._doctor_blocker()
    if blocker is not None:
        return ActionError(
            "doctor_blocker", f"Host check failed: {blocker.check}.", 409,
            blocker.fix or "Run the doctor checks and fix the host.",
            detail=blocker.message,
        )
    return None

def mount(self, target, *, label, size=None, recover=False, remove_hiberfile=False, confirm=None):
    if (error := self._guard()) is not None:
        return self._result("mount", error, label)
    try:
        with self.operation("mount", label):
            try:
                volume, volumes = self._resolve(target, label, size)
            except _Fail as exc:
                return self._result("mount", exc.error, label)
            if remove_hiberfile and confirm != label:
                error = ActionError(
                    "confirmation_mismatch",
                    f"Type {label!r} to confirm removing the hibernation file.",
                    400, "The label must match exactly.")
                return self._result("mount", error, label)
            try:
                mount_volume(
                    volume, uid=self.settings.owner[0], gid=self.settings.owner[1],
                    run=self._run, mounts=self._mounts, sleep=self._sleep,
                    recover=recover, remove_hiberfile=remove_hiberfile,
                    label_shared=self._shared(volume, volumes),
                )
            except MountFailed as exc:
                return self._mount_failure(exc, volume)
            self._flags.pop(volume_id(volume), None)
            return self._ok("mount", volume, f"{volume.device.label} is writable.", None)
    except OperationInProgress:
        return ActionResult(
            ok=False, message="Another operation is in progress.", restored=None,
            volume=None, error=ActionError(
                "operation_in_progress", "Another operation is in progress.", 409,
                "Wait for it to finish."))
```

`_current_view` re-discovers after the action so the response reflects reality:

```python
def _current_view(self, identifier: str):
    for candidate in self._discover():
        if volume_id(candidate) == identifier:
            return self._view(candidate)
    return None

def _result(self, action, error, label, restored=None):
    entry = self.activity_log.append(action, label, False, error.message, error.detail)
    self.events.publish("activity", asdict(entry))
    return ActionResult(ok=False, message=error.message, restored=restored,
                        volume=None, error=error)

def _ok(self, action, volume, message, restored):
    view = self._current_view(volume_id(volume)) or self._view(volume)
    entry = self.activity_log.append(action, view.label, True, message)
    self.events.publish("activity", asdict(entry))
    return ActionResult(ok=True, message=message, restored=restored,
                        volume=view, error=None)
```

Use `from dataclasses import asdict` for event payloads.

`_mount_failure` ends with:

```python
    entry = self.activity_log.append("mount", volume.device.label, False,
                                     message, str(exc))
    self.events.publish("activity", asdict(entry))
    view = self._current_view(volume_id(volume)) or self._view(volume)
    return ActionResult(False, message, restored, view, error)
```

`_mount_failure`:

```python
def _mount_failure(self, exc, volume):
    text = str(exc).lower()
    restored = True if "was restored" in text else False if "attempted" in text else None
    if "hibernat" in text:
        code, status, message, step = (
            "blocked_hibernated", 422,
            f"{volume.device.label} is hibernated by Windows.",
            "Mount with hiberfile removal, or shut Windows down fully.")
        self._flags.setdefault(volume_id(volume), {})["hibernated"] = True
    elif "dirty" in text or "unclean" in text:
        code, status, message, step = (
            "blocked_dirty", 422, f"{volume.device.label} is marked dirty.",
            "Mount with journal recovery, or run a Windows chkdsk.")
        self._flags.setdefault(volume_id(volume), {})["dirty"] = True
    elif "busy" in text or "in use" in text:
        code, status, message, step = (
            "busy", 409, f"{volume.device.label} is in use.",
            "Close anything using it, or unmount it first.")
    elif "permission" in text or "full disk access" in text:
        code, status, message, step = (
            "permission_denied", 403, "Permission denied opening the disk.",
            "Grant Full Disk Access to your terminal, then retry.")
    else:
        code, status, message, step = (
            "action_failed", 500, str(exc), None)
    error = ActionError(code, message, status, step, detail=str(exc),
                        volume=volume.device.label)
    entry = self.activity_log.append("mount", volume.device.label, False,
                                     message, str(exc))
    self.events.publish("activity", asdict(entry))
    view = self._current_view(volume_id(volume)) or self._view(volume)
    return ActionResult(False, message, restored, view, error)
```

`unmount` and `eject` mirror this: `_guard()` first, `operation("unmount"|"eject", label)`, `_resolve`, then:

- `unmount` — if `volume.state != "rw"`, return `not_mounted` 409. Otherwise call `unmount_volume(volume, run=self._run, mounts=self._mounts, sleep=self._sleep, keep_unmounted=False, label_shared=self._shared(volume, volumes))`; `restored = bool(returned)`. On `UnmountFailed`, map "busy"/"in use" to `busy` 409 and everything else to `action_failed` 500. When `restored` is false, return `ok=False`, `restored=False`, `action_failed` with next step `f"Run sudo diskutil mount {volume.device.device}"`.
- `eject` — if `volume.state != "unmounted"`, return `busy` 409 with "Unmount it first, then eject." Otherwise call `eject_volume(volume, run=self._run, mounts=self._mounts, sleep=self._sleep, label_shared=self._shared(volume, volumes))`; success message `f"{label} ejected."`; `UnmountFailed` maps to `action_failed`.

Both record activity with their own action name via `_ok` / `_result`. Shape:

```python
def unmount(self, target, *, label, size=None):
    if (error := self._guard()) is not None:
        return self._result("unmount", error, label)
    try:
        with self.operation("unmount", label):
            try:
                volume, volumes = self._resolve(target, label, size)
            except _Fail as exc:
                return self._result("unmount", exc.error, label)
            if volume.state != "rw":
                return self._result("unmount", ActionError(
                    "not_mounted", f"{label} is not mounted read-write.", 409,
                    "Nothing to unmount."), label)
            try:
                restored = unmount_volume(
                    volume, run=self._run, mounts=self._mounts, sleep=self._sleep,
                    keep_unmounted=False, label_shared=self._shared(volume, volumes),
                )
            except UnmountFailed as exc:
                text = str(exc).lower()
                busy = "busy" in text or "in use" in text
                return self._result("unmount", ActionError(
                    "busy" if busy else "action_failed", str(exc),
                    409 if busy else 500,
                    "Close anything using it, then retry." if busy else None,
                    detail=str(exc), volume=label), label)
            if not restored:
                return self._result("unmount", ActionError(
                    "action_failed",
                    f"{label} was unmounted, but the read-only mount could not be restored.",
                    500, f"Run sudo diskutil mount {volume.device.device}.",
                    detail="unmount succeeded; restore failed"), label, restored=False)
            return self._ok("unmount", volume, f"{label} is read-only.", True)
    except OperationInProgress:
        return ActionResult(
            ok=False, message="Another operation is in progress.", restored=None,
            volume=None, error=ActionError(
                "operation_in_progress", "Another operation is in progress.", 409,
                "Wait for it to finish."))
```

`eject` is the same shape with `operation("eject", label)`, the `state != "unmounted"` guard, `eject_volume(...)`, and on success records activity and returns `ActionResult(True, f"{label} ejected.", None, None, None)` — the device is gone, so there is no volume view to return.

`_shared`:

```python
def _shared(self, volume, volumes):
    return sum(1 for v in volumes if v.device.label == volume.device.label) > 1
```

`_result` and `_ok` take the action name, so activity records `mount`, `unmount`, or `eject` accurately.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_service.py -q`
Expected: 25 passed.

- [ ] **Step 5: Commit**

```bash
git add ntfsmnt/web/service.py tests/test_web_service.py
git commit -m "feat(web): action orchestration with identity and lock guards"
```

---

### Task 5: Pydantic schemas

**Files:**
- Create: `ntfsmnt/web/schemas.py`
- Create: `tests/test_web_schemas.py`

**Interfaces:**
- Consumes: `models` dataclasses.
- Produces: `StateModel`, `VolumeModel`, `ActivityModel`, `ActionResultModel`, `ErrorModel`, `ErrorResponse`, `ActionRequest`; builders `state_model(snapshot)`, `activity_model(entries)`, `result_model(result)`, `error_response(error)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_schemas.py
import pytest

pytest.importorskip("pydantic")

from ntfsmnt.web.models import (ActionError, ActionResult, DoctorStatus, HostView,
                                OwnerView, Snapshot, VolumeView)
from ntfsmnt.web.schemas import error_response, result_model, state_model


def snapshot():
    return Snapshot(
        generated_at="2026-10-22T14:00:00Z",
        host=HostView(True, False, OwnerView(501, 20), True, True),
        doctor=DoctorStatus("ok", "2026-10-22T14:00:00Z", ()),
        operation=None,
        volumes=(VolumeView(
            id="UUID-1", device="/dev/disk4s1", label="HD1", uuid="UUID-1",
            size=10, fs="ntfs", state="ro", mount_point="/Volumes/HD1",
            driver="fskit", pid=None),),
    )


def test_state_round_trips_the_snapshot():
    data = state_model(snapshot()).model_dump(mode="json")
    assert data["volumes"][0]["id"] == "UUID-1"
    assert data["host"]["owner"] == {"uid": 501, "gid": 20}
    assert data["operation"] is None


def test_error_response_keeps_next_step():
    error = ActionError("busy", "HD1 is in use.", 409, "Unmount it first.", "detail", "HD1")
    data = error_response(error).model_dump(exclude_none=True)
    assert data == {"error": {
        "code": "busy", "message": "HD1 is in use.", "next_step": "Unmount it first.",
        "detail": "detail", "volume": "HD1",
    }}


def test_result_model_includes_volume_and_error():
    error = ActionError("busy", "HD1 is in use.", 409)
    result = ActionResult(False, "HD1 is in use.", None, None, error)
    data = result_model(result).model_dump(mode="json", exclude_none=True)
    assert data["result"] == {"ok": False, "message": "HD1 is in use."}
    assert data["error"]["code"] == "busy"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_schemas.py -q`
Expected: import error for `ntfsmnt.web.schemas`.

- [ ] **Step 3: Implement `schemas.py`**

```python
# ntfsmnt/web/schemas.py
from typing import Literal

from pydantic import BaseModel, Field

from ntfsmnt.web.models import (ActionError, ActionResult, ActivityEntry,
                                DoctorStatus, HostView, OperationView, Snapshot,
                                VolumeView)


class OwnerModel(BaseModel):
    uid: int
    gid: int


class HostModel(BaseModel):
    root: bool
    read_only: bool
    owner: OwnerModel | None
    fuse_t: bool
    ntfs_3g: bool


class FindingModel(BaseModel):
    severity: Literal["warning", "blocker"]
    check: str
    message: str
    fix: str | None


class DoctorModel(BaseModel):
    status: Literal["ok", "warning", "blocker"]
    checked_at: str
    findings: list[FindingModel]


class ActionModel(BaseModel):
    name: str
    enabled: bool
    reason: str | None


class OptionModel(BaseModel):
    name: str
    enabled: bool
    guarded: bool
    reason: str | None


class VolumeModel(BaseModel):
    id: str
    device: str
    label: str
    uuid: str
    size: int
    fs: str
    state: str
    mount_point: str | None
    driver: str | None
    pid: int | None
    flags: dict[str, bool]
    actions: list[ActionModel]
    options: list[OptionModel]


class OperationModel(BaseModel):
    id: str
    action: str
    volume: str
    started_at: str


class StateModel(BaseModel):
    generated_at: str
    host: HostModel
    doctor: DoctorModel
    operation: OperationModel | None
    volumes: list[VolumeModel]


class ActivityEntryModel(BaseModel):
    id: int
    at: str
    action: str
    volume: str
    ok: bool
    summary: str
    detail: str | None


class ActivityModel(BaseModel):
    entries: list[ActivityEntryModel]


class ActionRequest(BaseModel):
    label: str = Field(min_length=0, max_length=255)
    size: int | None = None
    recover: bool = False
    remove_hiberfile: bool = False
    confirm: str | None = None


class ErrorModel(BaseModel):
    code: str
    message: str
    next_step: str | None = None
    detail: str | None = None
    volume: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorModel


class ResultModel(BaseModel):
    ok: bool
    message: str
    restored: bool | None = None


class ActionResultModel(BaseModel):
    result: ResultModel
    volume: VolumeModel | None = None
    operation: OperationModel | None = None
    error: ErrorModel | None = None
```

Add the builders with explicit field-by-field conversion (do not rely on `from_orm`, so the mapping is visible and testable). `_volume_model(view)` maps actions/options; `state_model`, `activity_model`, `result_model`, `error_response`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_schemas.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add ntfsmnt/web/schemas.py tests/test_web_schemas.py
git commit -m "feat(web): pydantic response schemas"
```

---

### Task 6: App, auth, read endpoints

**Files:**
- Create: `ntfsmnt/web/app.py`
- Create: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `Settings`, `Service`, `schemas`.
- Produces:
  - `create_app(settings: Settings, *, service: Service | None = None) -> FastAPI`
  - `require_session(settings, request)`, `require_mutation(settings, request)`
  - `GET /`, `GET /api/v1/state`, `GET /api/v1/activity`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_api.py
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ntfsmnt.doctor import Env
from ntfsmnt.web.app import create_app
from ntfsmnt.web.service import Service
from tests.web_helpers import FakeRun, make_settings, make_volume


def client(*, read_only=False):
    settings = make_settings(read_only=read_only)
    service = Service(
        settings,
        discover=lambda: [make_volume()],
        run=FakeRun(),
        mounts=lambda: [],
        doctor_env=Env(lambda p: True, lambda a: (0, ""), 0,
                       lambda p: "libfuse-t", lambda: None),
    )
    return TestClient(create_app(settings, service=service)), service


def test_missing_cookie_is_401():
    c, _ = client()
    r = c.get("/api/v1/state")
    assert r.status_code == 401


def test_token_exchange_sets_cookie_and_redirects():
    c, _ = client()
    r = c.get("/?token=tok", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    assert "ntfsmnt_session" in r.cookies


def test_wrong_token_is_401():
    c, _ = client()
    assert c.get("/?token=nope", follow_redirects=False).status_code == 401


def test_foreign_host_is_421():
    c, _ = client()
    c.get("/?token=tok")
    r = c.get("/api/v1/state", headers={"host": "evil.test"})
    assert r.status_code == 421


def test_state_shape_and_read_only_host():
    c, _ = client(read_only=True)
    c.get("/?token=tok")
    data = c.get("/api/v1/state").json()
    assert data["host"]["read_only"] is True
    assert data["volumes"][0]["id"] == "UUID-1"
    assert data["doctor"]["status"] == "ok"


def test_activity_is_empty_initially():
    c, _ = client()
    c.get("/?token=tok")
    assert c.get("/api/v1/activity").json() == {"entries": []}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: import error for `ntfsmnt.web.app`.

- [ ] **Step 3: Implement `app.py` (read half)**

```python
# ntfsmnt/web/app.py
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
```

FastAPI turns every `HTTPException` into the same `{"error": {...}}` envelope, so API clients never see Starlette's `{"detail": ...}`. The page route catches its own 401 to render HTML.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add ntfsmnt/web/app.py tests/test_web_api.py
git commit -m "feat(web): app factory, token auth, read endpoints"
```

---

### Task 7: Action endpoint (JSON)

**Files:**
- Modify: `ntfsmnt/web/app.py`
- Modify: `tests/test_web_api.py`

**Interfaces:**
- Consumes: `Service.mount/unmount/eject`, `require_mutation`, `result_model`, `error_response`.
- Produces: `POST /api/v1/volumes/{volume_id}/{action}` returning `ActionResultModel` or `ErrorResponse`.

- [ ] **Step 1: Write the failing tests**

```python
def auth(c):
    c.get("/?token=tok")
    return {"X-NTFSMNT": "1", "Origin": "http://testserver"}


def test_mutation_requires_header():
    c, _ = client()
    c.get("/?token=tok")
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1"}, headers={"Origin": "http://testserver"})
    assert r.status_code == 403


def test_mutation_requires_origin():
    c, _ = client()
    c.get("/?token=tok")
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1"}, headers={"X-NTFSMNT": "1"})
    assert r.status_code == 403


def test_unknown_action_is_404():
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/explode", json={"label": "HD1"}, headers=auth(c))
    assert r.status_code == 404


def test_guarded_mount_without_label_is_400(monkeypatch):
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(svc_mod, "mount_volume", lambda *a, **k: pytest.fail("called"))
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount",
               json={"label": "HD1", "remove_hiberfile": True}, headers=auth(c))
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "confirmation_mismatch"


def test_mount_success_returns_result_and_volume(monkeypatch):
    from ntfsmnt.mounter import MountResult
    import ntfsmnt.web.service as svc_mod
    monkeypatch.setattr(
        svc_mod, "mount_volume",
        lambda volume, **kw: MountResult(volume, "/Volumes/HD1", 4242))
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount", json={"label": "HD1"}, headers=auth(c))
    data = r.json()
    assert r.status_code == 200
    assert data["result"]["ok"] is True
    assert data["volume"]["label"] == "HD1"


def test_second_action_is_409_while_lock_held():
    c, svc = client()
    headers = auth(c)
    with svc.operation("mount", "HD1"):
        r = c.post("/api/v1/volumes/UUID-1/mount",
                   json={"label": "HD1"}, headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "operation_in_progress"


def test_wrong_label_is_volume_changed():
    c, _ = client()
    r = c.post("/api/v1/volumes/UUID-1/mount", json={"label": "HD2"}, headers=auth(c))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "volume_changed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: 404 for the missing route (except the unknown-action test).

- [ ] **Step 3: Implement the route**

Add `import logging` and `logger = logging.getLogger("ntfsmnt.web")` to `app.py`. Inside `create_app`, after the read routes:

```python
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
```

`_publish_state`:

```python
async def _publish_state(svc: Service) -> None:
    snap = await asyncio.to_thread(svc.snapshot)
    svc.events.publish("state", state_model(snap).model_dump(mode="json"))
```

Note: `svc.events.publish` is sync and loop-bound; fine on the loop.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_api.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add ntfsmnt/web/app.py tests/test_web_api.py
git commit -m "feat(web): JSON action endpoint"
```

---

### Task 8: SSE stream and reconcile tick

**Files:**
- Modify: `ntfsmnt/web/app.py`
- Create: `tests/test_web_sse.py`

**Interfaces:**
- Consumes: `EventBus`, `Service.snapshot`, `state_model`.
- Produces: `stream_frames(bus, last_event_id, ping_interval=20.0)` async generator; `reconcile_once(service, bus, last_json) -> str`; `GET /api/v1/events`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_sse.py
import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from ntfsmnt.web.app import reconcile_once, stream_frames
from ntfsmnt.web.events import EventBus


def test_stream_replays_history_first():
    async def go():
        bus = EventBus()
        bus.publish("state", {"a": 1})
        frames = stream_frames(bus, last_event_id=0, ping_interval=60)
        first = await asyncio.wait_for(frames.__anext__(), 1)
        assert first == 'id: 1\nevent: state\ndata: {"a": 1}\n\n'
        await frames.aclose()

    asyncio.run(go())


def test_stream_replays_only_after_last_id():
    async def go():
        bus = EventBus()
        bus.publish("a", {})
        bus.publish("b", {})
        frames = stream_frames(bus, last_event_id=1, ping_interval=60)
        assert "event: b" in await asyncio.wait_for(frames.__anext__(), 1)
        await frames.aclose()

    asyncio.run(go())


def test_reconcile_publishes_only_on_change():
    class Fake:
        busy = False
        def __init__(self):
            self.n = 0
        def snapshot(self):
            from ntfsmnt.web.models import (DoctorStatus, HostView, Snapshot)
            self.n += 1
            return Snapshot("now", HostView(True, False, None, True, True),
                            DoctorStatus("ok", "now", ()), None, ())

    async def go():
        bus = EventBus()
        fake = Fake()
        first = await reconcile_once(fake, bus, "")
        second = await reconcile_once(fake, bus, first)
        assert first != second
        assert second == first  # unchanged snapshot returns the same json
        await asyncio.sleep(0)
        assert len(bus.replay(0)) == 1  # only the changed one was published

    asyncio.run(go())


def test_reconcile_is_skipped_while_busy():
    class Fake:
        busy = True
        snapshot = None

    async def go():
        bus = EventBus()
        assert await reconcile_once(Fake(), bus, "last") == "last"
        assert bus.replay(0) == []

    asyncio.run(go())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_sse.py -q`
Expected: import error for `stream_frames`.

- [ ] **Step 3: Implement SSE + reconcile**

Add `import json` and `from fastapi.responses import StreamingResponse` to `app.py`, then:

```python
import json
from typing import AsyncIterator


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
    payload = state_model(snap).model_dump(mode="json")
    current = json.dumps(payload, sort_keys=True)
    if current != last_json:
        bus.publish("state", payload)
        return current
    return last_json
```

Route and lifespan:

```python
    @app.get("/api/v1/events")
    async def events(request: Request):
        require_session(settings, request)
        header = request.headers.get("last-event-id", "0")
        last = int(header) if header.isdigit() else 0
        return StreamingResponse(
            stream_frames(svc.events, last),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.on_event("startup")
    async def start_reconcile():
        svc.events.bind(asyncio.get_running_loop())
        async def loop():
            last = ""
            while True:
                await asyncio.sleep(5)
                if svc.events.has_subscribers():
                    last = await reconcile_once(svc, svc.events, last)
        app.state.reconcile = asyncio.create_task(loop())

    @app.on_event("shutdown")
    async def stop_reconcile():
        task = getattr(app.state, "reconcile", None)
        if task:
            task.cancel()
```

Use `lifespan` if `on_event` is deprecated in the installed FastAPI; the implementer may use `@asynccontextmanager lifespan`. Either is acceptable as long as tests pass.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_sse.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add ntfsmnt/web/app.py tests/test_web_sse.py
git commit -m "feat(web): SSE stream and reconcile tick"
```

---

### Task 9: Templates, CSS, htmx fragment responses

**Files:**
- Create: `ntfsmnt/web/page/index.html`, `_region.html`, `_list.html`, `_detail.html`, `_activity.html`, `app.css`, `htmx.min.js`
- Create: `tests/web_render.py`
- Modify: `ntfsmnt/web/app.py`
- Create: `tests/test_web_templates.py`

**Interfaces:**
- Consumes: `StateModel`, `VolumeModel`, `ActionModel`, `OptionModel`, `ErrorModel`.
- Produces: `GET /partials/volumes?selected=<id>` and htmx HTML responses from `POST /api/v1/volumes/{id}/{action}` when `HX-Request: true`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_templates.py
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from ntfsmnt.web.schemas import StateModel
from tests.web_render import render_detail, render_index


def state(label="HD1", *, state="ro", actions=None, options=None):
    return StateModel.model_validate({
        "generated_at": "2026-10-22T14:00:00Z",
        "host": {"root": True, "read_only": False, "owner": {"uid": 501, "gid": 20},
                 "fuse_t": True, "ntfs_3g": True},
        "doctor": {"status": "ok", "checked_at": "now", "findings": []},
        "operation": None,
        "volumes": [{
            "id": "UUID-1", "device": "/dev/disk4s1", "label": label,
            "uuid": "UUID-1", "size": 10, "fs": "ntfs", "state": state,
            "mount_point": "/Volumes/HD1", "driver": "fskit", "pid": None,
            "flags": {}, "actions": actions or [], "options": options or [],
        }],
    })


def test_hostile_label_is_escaped_everywhere():
    html = render_index(state('<img src=x onerror=alert(1)>'))
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_read_only_chip_is_text_not_colour_only():
    html = render_detail(state("HD1"))
    assert "read-only" in html


def test_disabled_action_renders_reason():
    html = render_detail(state(actions=[
        {"name": "mount", "enabled": False, "reason": "already writable"}]))
    assert "disabled" in html
    assert "already writable" in html


def test_guarded_option_has_confirm_trigger():
    html = render_detail(state(options=[
        {"name": "remove_hiberfile", "enabled": True, "guarded": True,
         "reason": "Windows hibernation state will be discarded."}]))
    assert "data-confirm-trigger" in html
    assert "Type HD1 to confirm" in html


def test_empty_state_explains_what_to_do():
    empty = state().model_copy(update={"volumes": []})
    html = render_index(empty)
    assert "No NTFS volumes" in html


def test_index_has_live_regions_and_script():
    html = render_index(state())
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert "/static/app.js" in html
```

`tests/web_render.py` is a test-only helper that builds the same `Jinja2Templates(directory=PAGE_DIR)` environment and exposes `render_index(state)` / `render_detail(state)`. It is imported only by tests, so the production package keeps no test scaffolding.

```python
# tests/web_render.py
from fastapi.templating import Jinja2Templates

from ntfsmnt.web.app import PAGE_DIR

templates = Jinja2Templates(directory=PAGE_DIR)


def _context(state):
    return {
        "state": state,
        "selected": state.volumes[0].id if state.volumes else None,
        "activity": (),
        "error": None,
    }


def render_index(state):
    return templates.get_template("index.html").render(**_context(state))


def render_detail(state):
    return templates.get_template("_detail.html").render(**_context(state))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_templates.py -q`
Expected: `ModuleNotFoundError: No module named 'tests.web_render'`.

- [ ] **Step 3: Write the templates**

`index.html`: doctype, `<link rel="stylesheet" href="/static/app.css">`, header with wordmark, root/read-only badge, doctor line, live indicator; an always-present empty `<div id="alert" role="alert">` for action errors and blockers; `<main id="app">` includes `_region.html`; `<div id="activity-live" aria-live="polite">`; `<script src="/static/htmx.min.js" defer></script>` and `<script src="/static/app.js" defer></script>`. Vendor htmx 2.0.4 into `page/htmx.min.js` and serve it from `/static/`; no CDN at runtime, because this page runs as root.

`_list.html`: `<nav id="volume-list" aria-label="NTFS volumes">` with one `<button class="volume-row" data-id="{{ v.id }}" data-state="{{ v.state }}">` per volume, showing label, chip (`<span class="chip chip--{{ v.state }}">{{ v.state }}</span>`), and device/mount point. Selected row gets `aria-current="true"` and `.is-selected`. When there are no volumes, render `<p class="empty">No NTFS volumes are attached. Connect a drive and it appears here.</p>` instead of an empty list.

`_detail.html`: a root `<section id="detail">`; when no volume is selected, render `<p class="empty">Select a drive to see its actions.</p>`. Otherwise: title, chip, metadata line, primary action forms (`hx-post="/api/v1/volumes/{{ v.id }}/mount"`, `hx-headers='{"X-NTFSMNT":"1"}'`, hidden `label` and `size`, `hx-target="#detail"`, `hx-swap="outerHTML"`), disabled buttons with inline reason, `<details>` Advanced with `recover` and `remove_hiberfile` checkboxes, inline confirm block for guarded options (`data-confirm-form`, `data-label="{{ v.label }}"`) containing the warning, the exact prompt `Type {{ v.label }} to confirm`, a labelled input, command preview `sudo ntfsmnt mount {{ v.label }} --remove-hiberfile --confirmed`, Confirm (disabled) and Cancel link, then the activity list.

`_activity.html`: rows with time, action, label, outcome; `detail` in a `<details>`; no `|safe`.

Chip copy: `writable` for `rw`, `read-only` for `ro`, `unmounted` for `unmounted`, `error` for `error`.

CSS: the tokens from the spec, 38% left column with min/max widths, 900 px breakpoint, 2 px focus ring, `@media (prefers-reduced-motion: reduce)` disabling transitions. No shadows. System fonts with `Iowan Old Style`/`Georgia` headings and `SF Mono`/`ui-monospace` log.

- [ ] **Step 4: Wire templates and the htmx branch**

In `app.py`:

```python
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=PAGE_DIR)

async def _state_for(svc):
    return state_model(await asyncio.to_thread(svc.snapshot))

async def _detail_response(request, svc, selected, error=None):
    return templates.TemplateResponse(
        request, "_detail.html",
        {"state": await _state_for(svc), "selected": selected,
         "activity": svc.activity(), "error": error},
    )

async def _region_response(request, svc, state, selected):
    return templates.TemplateResponse(
        request, "_region.html",
        {"state": state, "selected": selected,
         "activity": svc.activity(), "error": None},
    )
```

`GET /partials/volumes` requires the session, reads `selected` (default first volume), and returns `_region.html` — a `<div id="volumes-region">` containing the list and the detail. That is what the SSE client swaps. The POST htmx response returns `_detail.html` only, so the action result and its inline error render in `#detail` immediately; the subsequent `state` event refreshes the whole region.

Replace the placeholder in `GET /` (Task 6) with the real page:

```python
        snap = await _state_for(svc)
        return templates.TemplateResponse(
            request, "index.html",
            {"state": snap,
             "selected": snap.volumes[0].id if snap.volumes else None,
             "activity": svc.activity(), "error": None},
        )
```

```python
    @app.get("/partials/volumes")
    async def partial(request: Request, selected: str | None = None):
        require_session(settings, request)
        snap = await _state_for(svc)
        chosen = selected or (snap.volumes[0].id if snap.volumes else "")
        return await _region_response(request, svc, snap, chosen)
```

In the POST route, after `result`:

```python
        if request.headers.get("hx-request"):
            return await _detail_response(
                request, svc,
                result.volume.id if result.volume else volume_id,
                error=result.error,
            )
```

Always 200 for htmx, so the fragment swaps and the error renders inline. JSON clients still get the real status.

`_detail.html` renders the activity list inside the detail panel, so a state refresh carries new activity with it. No separate activity fragment endpoint is needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_web_templates.py -q`
Expected: 6 passed.

- [ ] **Step 6: Commit**

```bash
git add ntfsmnt/web/page tests/web_render.py ntfsmnt/web/app.py tests/test_web_templates.py
git commit -m "feat(web): master-detail templates and htmx responses"
```

---

### Task 10: Client behaviour

**Files:**
- Create: `ntfsmnt/web/page/app.js`

**Interfaces:**
- Consumes: the DOM ids/classes from Task 9, `/api/v1/events`.
- Produces: no Python interface.

- [ ] **Step 1: Implement the client**

- `new EventSource("/api/v1/events")`; on `state`, if no `[data-confirm-open]` exists, call `htmx.ajax("GET", "/partials/volumes?selected=" + encodeURIComponent(selectedId), {target: "#volumes-region", swap: "outerHTML"})`; otherwise set `pendingRefresh = true`. `selectedId` is `document.querySelector(".volume-row.is-selected")?.dataset.id`.
- On `activity`, do nothing: the action publishes `activity` immediately before `state`, and the state refresh re-renders the activity list inside the detail panel. This avoids building HTML from JSON (an XSS vector).
- Confirm UX: delegated click on `[data-confirm-trigger]` → unhide `[data-confirm-form]`, set `data-confirm-open`, focus the input. Input listener compares to `data-label` and toggles `disabled` on the confirm button. `Esc` and Cancel hide, restore focus to the trigger, and flush `pendingRefresh` if set.
- Live indicator: `EventSource.onopen` adds `.is-live`; `onerror` removes it and shows "Lost connection to ntfsmnt. Retrying…".
- `document.addEventListener("visibilitychange", ...)` → on visible, `htmx.ajax` refresh.
- Respect `prefers-reduced-motion` in CSS only.

- [ ] **Step 2: Verify manually**

Run: `uv run pytest -q` (all green), then `uv run python -m ntfsmnt web` as non-root and confirm:
- the read-only banner shows;
- clicking a volume swaps the detail;
- the guarded option reveals the confirm form, Confirm stays disabled until the exact label, `Esc` closes it and returns focus;
- the live dot dims when the server stops.

- [ ] **Step 3: Commit**

```bash
git add ntfsmnt/web/page/app.js
git commit -m "feat(web): SSE client and inline confirm behaviour"
```

---

### Task 11: CLI `web` subcommand, docs, acceptance

**Files:**
- Modify: `ntfsmnt/cli.py`
- Modify: `ntfsmnt/web/app.py` (add `run_server`)
- Modify: `README.md`
- Modify: `tests/manual/acceptance.md`
- Create: `tests/test_web_cli.py`

**Interfaces:**
- Consumes: `settings_from`, `with_port`, `create_app`.
- Produces: `ntfsmnt web [--port N] [--open] [--owner uid:gid]`, `run_server(settings)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web_cli.py
import sys

import pytest

pytest.importorskip("fastapi")

from ntfsmnt.cli import _build_parser, main
import ntfsmnt.web.app as web_app


def test_parser_accepts_web_flags():
    args = _build_parser().parse_args(
        ["web", "--port", "9000", "--open", "--owner", "501:20"])
    assert (args.command, args.port, args.open, args.owner) == ("web", 9000, True, "501:20")


def test_missing_extra_prints_install_hint(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ntfsmnt.web.app", None)
    assert main(["web"]) == 1
    assert "ntfsmnt[web]" in capsys.readouterr().err


def test_bad_owner_exits_2(monkeypatch, capsys):
    monkeypatch.setattr(web_app, "run_server", lambda settings: None)
    assert main(["web", "--owner", "501"]) == 2
    assert "uid:gid" in capsys.readouterr().err


def test_web_passes_read_only_settings(monkeypatch):
    seen = {}
    monkeypatch.setattr(web_app, "run_server", lambda settings: seen.update(
        root=settings.root, read_only=settings.read_only, owner=settings.owner))
    monkeypatch.setattr("os.geteuid", lambda: 501)
    monkeypatch.setattr("os.environ", {})
    assert main(["web", "--port", "0"]) == 0
    assert seen == {"root": False, "read_only": True, "owner": None}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_web_cli.py -q`
Expected: argparse error for the `web` subcommand.

- [ ] **Step 3: Add the CLI command**

In `_build_parser`:

```python
    web = sub.add_parser("web", help="serve the local web UI")
    web.add_argument("--port", type=int, default=0)
    web.add_argument("--open", action="store_true")
    web.add_argument("--owner", default=None, metavar="UID:GID")
```

In `cli.py`:

```python
def _cmd_web(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    try:
        from ntfsmnt.web.app import run_server
        from ntfsmnt.web.config import settings_from
    except ImportError:
        print(
            "ntfsmnt: the web UI needs optional dependencies; "
            "install with `uv tool install 'ntfsmnt[web]'`",
            file=sys.stderr,
        )
        return 1
    try:
        settings = settings_from(
            port=args.port, owner=args.owner, environ=os.environ,
            euid=os.geteuid(), open_browser=args.open,
        )
    except ValueError as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 2
    run_server(settings)
    return 0
```

Register `"web": _cmd_web` in `COMMANDS`.

- [ ] **Step 4: Add `run_server`**

In `app.py`:

```python
def run_server(settings: Settings) -> None:
    import socket
    import uvicorn
    from ntfsmnt.web.config import with_port

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((settings.host, settings.port))
    port = sock.getsockname()[1]
    settings = with_port(settings, port)
    url = f"http://127.0.0.1:{port}/?token={settings.token}"
    print(f"ntfsmnt web: {url}", flush=True)
    if settings.open_browser:
        import webbrowser
        webbrowser.open(url)
    config = uvicorn.Config(
        create_app(settings), host=settings.host, port=port,
        log_level="warning", access_log=False,
    )
    uvicorn.Server(config).run(sockets=[sock])
```

`settings.host` does not exist yet; add `host: str = "127.0.0.1"` to `Settings` in Task 1 (do it when implementing Task 1).

- [ ] **Step 5: Update docs**

`README.md`: add a "Web UI" section — `sudo ntfsmnt web --open`, what the token is, read-only mode, that closing the page never unmounts, and that the CLI remains the source of truth.

`tests/manual/acceptance.md`: append the seven manual steps from spec §11 (page matches `list`, mount/write/unmount/eject, guarded label, non-root, clean exit).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -q`
Expected: 153 CLI tests + all web tests pass.

Run: `uv run python -m ntfsmnt --help`
Expected: `web` appears in the subcommand list.

- [ ] **Step 7: Commit**

```bash
git add ntfsmnt/cli.py ntfsmnt/web/app.py README.md tests/manual/acceptance.md tests/test_web_cli.py
git commit -m "feat(web): ntfsmnt web subcommand and acceptance docs"
```

---

## Self-Review

- **Spec coverage:** shape (T11), privilege (T1/T3/T6), security (T1/T6/T7/T9), API (T5–T8), freshness (T8), visual (T9/T10), invariants (T4/T9), testing (every task), acceptance (T11).
- **Type consistency:** `Settings` fields match `config.py`; `Service` methods return `ActionResult`; `state_model` consumes `Snapshot`; templates consume `StateModel`.
- **Known deviation:** `Service` publishes `operation`/`activity` events itself (thread-safe `EventBus.publish`); the app publishes `state` after each action. This keeps the lock and the events in one place.
- **Risk:** `@app.on_event` is deprecated in recent FastAPI. If it warns, switch to a `lifespan` context manager; the reconcile test calls `reconcile_once` directly, so the route test is unaffected.
