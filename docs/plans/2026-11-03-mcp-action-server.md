# ntfsmnt MCP action server — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `ntfsmnt mcp` from a read-only reporter into a grant-scoped actor: an agent can mount, unmount, and eject, but only inside a narrow, expiring, human-approved grant, and every action lands in a root-owned audit log.

**Architecture:** The stdio MCP adapter stays unprivileged and disposable. On first action it spawns the existing root `web` server in `--mcp-session` mode, receives an agent token on the child's stdout pipe, and talks HTTP over loopback. Grants, the cross-process lock, and the audit log live in `ntfsmnt/control/`, not in the adapter, so a future daemon (Option B) is a packaging change.

**Tech Stack:** Python ≥3.12, stdlib only for the control layer and adapter (`json`, `os`, `fcntl`, `urllib.request`, `secrets`, `threading`), FastAPI + Jinja for the approval page, pytest with injected clocks/runners, Swift `LAContext` for the optional Touch ID helper.

**Spec:** `docs/specs/2026-11-03-mcp-action-server.md`

**Rendered spec:** `docs/mcp-action-server.html`

## Global Constraints

- Do not weaken any invariant in `AGENTS.md`. In particular: no `osascript`, no repair tools, no `SIGKILL` of disk-related processes, no write without a probe, no guessed volume on ambiguity.
- `--recover` and `--remove-hiberfile` are never grantable. The refusal is a test, not a comment.
- No new runtime dependency in the base install. `[web]` stays the only extra.
- Every test runs without root, without `/Volumes` writes, and with injected callables (invariant 10). Tests must never touch `/var/db` or `/var/run`; inject temp paths.
- stdout of `ntfsmnt mcp` is JSON-RPC only. Diagnostics go to stderr.
- Keep the existing single-token, cookie-based dashboard working unchanged. The agent token is additive.
- `uv run pytest -q` stays green after every task. Commit after each task; do not push.
- Branch: `feat/mcp-action-server`, created from `spec/mcp-action-server` (which
  holds the spec and plan commits).

## File Structure

| File | Responsibility |
|---|---|
| `ntfsmnt/control/__init__.py` | Re-export `GrantStore`, `Grant`, `GrantRequest`, `GrantError`, `AuditLog`, `NullAudit`, `OperationFileLock` |
| `ntfsmnt/control/grants.py` | In-memory grant lifecycle: request, approve, deny, check, revoke, expire |
| `ntfsmnt/control/audit.py` | Append-only JSONL writer plus a no-op implementation for tests |
| `ntfsmnt/control/lock.py` | Cross-process `flock` around one mutation |
| `ntfsmnt/web/grants.py` | HTTP routes for grants and the approval page |
| `ntfsmnt/web/auth.py` | Adds `require_agent()`; existing cookie helpers untouched |
| `ntfsmnt/web/page/approve.html` | Human approval page, no JavaScript |
| `ntfsmnt/web/service.py` | Owns `GrantStore`, audit, and the file lock; one execution path for humans and agents |
| `ntfsmnt/web/config.py` | `agent_token`, `mcp_session`, `exit_on_stdin_eof` |
| `ntfsmnt/web/app.py` | Wires grant routes, the agent branch on the action route, audit after actions |
| `ntfsmnt/mcp_client.py` | Spawns the controller, parses its handshake, HTTP client, approval opener |
| `ntfsmnt/mcp.py` | Nine tools; read tools stay local, action tools delegate to the controller |
| `ntfsmnt/demo.py` | Download + verify + attach the demo image |
| `app/Approve.swift` | Optional Touch ID helper (`LAContext`) |
| `server.json` | MCP registry manifest |
| `docs/_spec-template.html`, `scripts/render-spec.sh` | Reproducible spec → HTML rendering |
| `tests/test_control_grants.py`, `test_control_audit.py`, `test_control_lock.py`, `test_web_grants.py`, `test_web_agent_auth.py`, `test_mcp_client.py`, `test_mcp_actions.py`, `test_demo.py`, `test_server_json.py` | New coverage |
| `AGENTS.md`, `docs/AGENT.md`, `README.md`, `site/llms.txt` | Truth updates in Task 15 |

## Phase 1 — control layer

### Task 1: Audit writer

**Files:**
- Create: `ntfsmnt/control/__init__.py`, `ntfsmnt/control/audit.py`
- Test: `tests/test_control_audit.py`

**Interfaces:**
```python
class AuditUnavailable(Exception): ...

class NullAudit:
    def append(self, event: str, **fields) -> None: ...
    def read(self, limit: int = 200) -> list[dict]: ...

class AuditLog:
    def __init__(self, path: Path = Path("/var/db/ntfsmnt/audit.jsonl"),
                 *, clock: Callable[[], datetime] = utcnow) -> None: ...
    def append(self, event: str, **fields) -> None: ...  # raises AuditUnavailable
    def read(self, limit: int = 200) -> list[dict]: ...
```

- [ ] Write `tests/test_control_audit.py`:
  - `test_append_writes_one_json_line_per_event` — temp path, two events, `read()` returns both with `ts` and `event`.
  - `test_file_is_created_0600` — `stat().st_mode & 0o777 == 0o600`.
  - `test_read_returns_last_n` — append five, `read(2)` returns the last two.
  - `test_missing_directory_raises_audit_unavailable` — path under a nonexistent dir.
  - `test_null_audit_accepts_everything` — `NullAudit().append("x", a=1)` and `read() == []`.
- [ ] Run `uv run pytest -q tests/test_control_audit.py` — expect collection failure (module missing).
- [ ] Implement with `os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)`, one `os.write` per serialized line, `threading.Lock` around it. `read()` tolerates a truncated final line by skipping it.
- [ ] Run the file's tests, then `uv run pytest -q`.
- [ ] Commit: `feat(control): append-only audit log with a no-op test double`

### Task 2: Cross-process operation lock

**Files:**
- Create: `ntfsmnt/control/lock.py`
- Test: `tests/test_control_lock.py`

**Interfaces:**
```python
class FileLockBusy(Exception): ...

class OperationFileLock:
    def __init__(self, path: Path = Path("/var/run/ntfsmnt.lock")) -> None: ...
    def acquire(self) -> bool: ...      # LOCK_EX | LOCK_NB, keeps the fd
    def release(self) -> None: ...
    def __enter__(self) -> "OperationFileLock": ...
    def __exit__(self, *exc) -> None: ...
```

- [ ] Write `tests/test_control_lock.py`:
  - `test_acquire_creates_file` — temp path.
  - `test_second_acquire_on_a_second_fd_is_busy` — open the same path with a second `OperationFileLock`; `acquire()` is `False` while the first holds it (`flock` is per file description).
  - `test_release_allows_reacquire`.
  - `test_context_manager_releases_on_exception`.
- [ ] Run; expect failure.
- [ ] Implement with `fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)`; on `BlockingIOError` close and return `False`. `release()` unlocks and closes. A missing parent directory raises `FileLockBusy` rather than crashing the request.
- [ ] Run, then full suite.
- [ ] Commit: `feat(control): cross-process flock with a busy answer, not a wait`

### Task 3: Grant store

**Files:**
- Create: `ntfsmnt/control/grants.py`
- Test: `tests/test_control_grants.py`

**Interfaces:**
```python
MIN_TTL, MAX_TTL, DEFAULT_TTL, MAX_PENDING = 60, 1800, 900, 3
GRANTABLE = frozenset({"mount", "unmount", "eject"})

@dataclass(frozen=True)
class VolumeBinding:
    uuid: str | None
    device: str
    label: str
    @classmethod
    def of(cls, volume: Volume) -> "VolumeBinding": ...
    def matches(self, volume: Volume) -> bool: ...   # uuid when both have one, else device+label

@dataclass(frozen=True)
class GrantRequest:
    id: str; nonce: str; status: str          # pending | approved | denied
    binding: VolumeBinding; actions: frozenset[str]
    ttl_seconds: int; reason: str; client: str
    created_at: datetime

@dataclass(frozen=True)
class Grant:
    id: str; binding: VolumeBinding; actions: frozenset[str]
    created_at: datetime; expires_at: datetime
    reason: str; client: str

class GrantError(Exception):
    code: str   # grant_required | grant_expired | grant_revoked
                # grant_mismatch | grant_action_mismatch | too_many_requests

class GrantStore:
    def __init__(self, *, clock: Callable[[], datetime] = utcnow,
                 max_pending: int = MAX_PENDING) -> None: ...
    def request(self, volume: Volume, actions: Sequence[str], *,
                ttl_seconds: int = DEFAULT_TTL, reason: str,
                client: str) -> GrantRequest: ...
    def pending(self, request_id: str) -> GrantRequest | None: ...
    def approve(self, request_id: str) -> Grant: ...
    def deny(self, request_id: str) -> None: ...
    def grant(self, grant_id: str) -> Grant | None: ...
    def check(self, grant_id: str | None, volume: Volume, action: str) -> Grant: ...
    def revoke(self, grant_id: str) -> bool: ...
    def active(self) -> list[Grant]: ...
    def expire(self) -> None: ...
```

- [ ] Write `tests/test_control_grants.py` with a `Clock` helper (`now = [datetime(2026,11,3,10,0,tzinfo=timezone.utc)]`; `clock = lambda: now[0]`) and `make_volume()` from `tests.web_helpers`:
  - `test_request_rejects_destructive_actions` — `["recover"]`, `["remove_hiberfile"]` raise `ValueError` with `manual_required`.
  - `test_request_clamps_ttl` — 5 → 60, 99999 → 1800, default 900.
  - `test_approve_mints_a_grant_and_never_reuses_the_request_id`.
  - `test_mount_implies_unmount` — request `["mount"]`, approve, `check(gid, volume, "unmount")` passes.
  - `test_check_rejects_wrong_action`, `test_check_rejects_another_volume`, `test_check_rejects_expired` (advance clock past TTL), `test_check_rejects_revoked`.
  - `test_pending_cap_and_dedupe` — three distinct pending requests pass, the fourth raises `too_many_requests`; repeating the same `(volume, actions)` returns the same `id` and `nonce`.
  - `test_deny_and_timeout_statuses` — denied request cannot be approved; `pending()` reports `denied`.
  - `test_active_prunes_expired`.
- [ ] Run; expect failure.
- [ ] Implement. IDs: `f"g_{secrets.token_urlsafe(12)}"` / `f"r_{...}"`; nonce: `secrets.token_urlsafe(32)`. `check()` order: missing → `grant_required`; revoked → `grant_revoked`; expired → `grant_expired`; binding → `grant_mismatch`; action → `grant_action_mismatch`.
- [ ] Run, then full suite.
- [ ] Commit: `feat(control): grant store with TTL, volume binding, and no destructive verbs`

## Phase 2 — HTTP surface and approval

### Task 4: Settings for the MCP session

**Files:**
- Modify: `ntfsmnt/web/config.py`, `ntfsmnt/web/app.py` (one line: pass new fields)
- Test: `tests/test_web_config.py`

**Interfaces:**
```python
@dataclass(frozen=True)
class Settings:
    ...
    agent_token: str | None = None
    mcp_session: bool = False
    exit_on_stdin_eof: bool = False

def settings_from(..., mcp_session: bool = False,
                  exit_on_stdin_eof: bool = False) -> Settings
```

- [ ] Add tests:
  - `test_mcp_session_mints_an_agent_token_but_still_has_a_human_token` — both non-empty, different.
  - `test_normal_mode_has_no_agent_token` — `agent_token is None`.
  - `test_mcp_session_with_explicit_token_keeps_it`.
- [ ] Run; expect failure.
- [ ] Implement; never derive the agent token from the human token.
- [ ] Run, then full suite.
- [ ] Commit: `feat(web): mint an agent token only in MCP session mode`

### Task 5: Agent authentication

**Files:**
- Modify: `ntfsmnt/web/auth.py`
- Test: `tests/test_web_agent_auth.py`

**Interfaces:**
```python
def require_agent(settings: Settings, request: Request) -> None: ...
def is_agent_request(request: Request) -> bool: ...   # Authorization: Bearer ...
```

- [ ] Tests:
  - `test_agent_token_in_bearer_header_is_accepted`.
  - `test_missing_header_is_401`.
  - `test_wrong_scheme_is_401`.
  - `test_human_token_is_not_an_agent_token` (paste `settings.token` as bearer → 401).
  - `test_agent_token_is_not_a_session_cookie` (agent token in the cookie → 401 on a cookie route).
- [ ] Run; expect failure.
- [ ] Implement with `hmac.compare_digest`; `require_agent` raises `HTTPException(401, "agent token required")` and never falls back to the cookie.
- [ ] Run, then full suite.
- [ ] Commit: `feat(web): bearer agent token, separate from the dashboard session`

### Task 6: Grant routes

**Files:**
- Create: `ntfsmnt/web/grants.py`
- Modify: `ntfsmnt/web/app.py` (`register_grant_routes(app, settings, svc, templates)` next to `register_agent_routes`), `ntfsmnt/web/service.py` (`grants: GrantStore | None = None` on `Service.__init__`; add `grant_lookup`)
- Test: `tests/test_web_grants.py`

**Interfaces:**
```python
def register_grant_routes(app, settings, svc, templates) -> None: ...

Service(..., grants: GrantStore | None = None)
Service.grant_lookup(selector: str) -> Volume   # label, UUID, device, or mount point

POST   /api/v1/grants                       # agent
GET    /api/v1/grants/{request_id}          # agent
GET    /api/v1/grants                       # agent
DELETE /api/v1/grants/{grant_id}            # agent
```

Request body model `GrantRequestBody`: `volume: str`, `actions: list[str]`, `ttl_seconds: int = 900`, `reason: str = ""`, `client: str = "mcp"`.

Responses:
```json
{"request_id":"r_...", "status":"pending", "approval_url":"http://127.0.0.1:PORT/approve/r_...?nonce=...", "wait_seconds":90}
{"request_id":"r_...", "status":"approved", "grant_id":"g_..."}
{"grants":[{"grant_id":"g_...","volume":"HD1","device":"/dev/disk4s1","actions":["mount","unmount"],"expires_at":"..."}]}
{"revoked":true}
```

- [ ] Tests (build the client with `make_settings(agent_token="agent")` added to `tests/web_helpers.py`, and inject `GrantStore(clock=...)`):
  - `test_request_grant_returns_a_pending_request_and_a_nonce_url`.
  - `test_request_grant_rejects_recover_with_manual_required` (400, code `manual_required`, includes `ntfsmnt mount --recover`).
  - `test_request_grant_rejects_unknown_volume` (404 `volume_not_found`).
  - `test_poll_reports_pending_then_approved` (approve through the store, then poll).
  - `test_list_and_revoke`.
  - `test_routes_require_the_agent_token`.
- [ ] Run; expect failure.
- [ ] Implement. `svc.grant_lookup(selector)` resolves with `ntfsmnt.volumes.resolve` and raises `_Fail(ActionError("ambiguous_volume"|"volume_not_found", ..., 404))`. `approval_url` uses `settings.host`, the live port, and the request nonce; nothing else.- [ ] Run, then full suite.
- [ ] Commit: `feat(web): grant request, poll, list, and revoke routes`

### Task 7: Approval page and nonce

**Files:**
- Create: `ntfsmnt/web/page/approve.html`
- Modify: `ntfsmnt/web/grants.py`
- Test: `tests/test_web_grants.py`

**Interfaces:**
```
GET  /approve/{request_id}?nonce=...                # no cookie
POST /api/v1/grants/{request_id}/approve            # X-NTFSMNT-APPROVAL
POST /api/v1/grants/{request_id}/deny
```

- [ ] Tests:
  - `test_approval_page_lists_volume_actions_ttl_and_reason`.
  - `test_approval_page_rejects_a_wrong_nonce` (403, no details).
  - `test_approve_requires_nonce_and_origin`.
  - `test_approve_rejects_an_agent_token` — a valid bearer alone cannot approve; 403 `human_approval_required`.
  - `test_nonce_is_single_use` — second approve is 403 `approval_consumed`.
  - `test_deny_marks_the_request_denied`.
  - `test_page_sets_referrer_policy_no_referrer`.
- [ ] Run; expect failure.
- [ ] Implement. The page is one small form, no JS, no inline event handlers. Nonce compared in constant time. Approve/deny audit through `svc.audit`; an audit failure returns 503 `audit_unavailable` and does not mint a grant.
- [ ] Run, then full suite.
- [ ] Commit: `feat(web): single-use nonce approval page for grants`

### Task 8: One execution path for agents and humans

**Files:**
- Modify: `ntfsmnt/web/app.py` (action route), `ntfsmnt/web/service.py` (`grant_lookup`, `check_grant`, file-lock acquire inside `operation()`, audit on every action)
- Test: `tests/test_web_grants.py`, `tests/test_web_service.py`

**Interfaces:**
```python
Service(..., audit: AuditLog | NullAudit | None = None,
        file_lock: OperationFileLock | None = None)

Service.check_grant(grant_id: str | None, volume: Volume, action: str) -> Grant
```

Action request, agent branch:
```
Authorization: Bearer <agent_token>
X-NTFSMNT: 1
X-NTFSMNT-GRANT: g_...
Origin: http://127.0.0.1:<port>
```

- [ ] Tests:
  - `test_agent_mount_without_grant_is_403_grant_required`.
  - `test_agent_mount_with_a_grant_runs_the_same_service_call` — `FakeRun` sees the same argv a human mount produces.
  - `test_agent_remove_hiberfile_is_refused_before_any_runner_call` — 403 `manual_required`, `FakeRun.calls == []`.
  - `test_grant_for_one_volume_cannot_mount_another`.
  - `test_operation_in_progress_when_the_file_lock_is_held` — inject `OperationFileLock(tmp_path)` and hold it in a second instance.
  - `test_successful_mount_appends_mount_ok` and `test_failed_mount_appends_mount_fail` — `NullAudit` replaced by `AuditLog(tmp)`.
  - `test_audit_failure_blocks_mount_before_any_runner_call` — the pre-action line is written first, so `FakeRun.calls == []` and the response is 503 `audit_unavailable`.
  - `test_audit_failure_after_success_still_reports_success` — the outcome line raises, the mount result is returned, and the failure goes to stderr. Never hide a real mount.
  - `test_audit_failure_does_not_block_unmount` — unmount proceeds because leaving a volume mounted is worse.
- [ ] Run; expect failure.
- [ ] Implement. The agent branch resolves the grant **before** the service call and rejects destructive body fields first. `operation()` tries the file lock first, then the in-process lock; either busy → `OperationInProgress`. Audit is two-phase: `<action>.attempt` before the runner, `<action>.ok|fail` after it.
- [ ] Wire the production default: `create_app` builds
  `Service(settings, audit=AuditLog(), file_lock=OperationFileLock() if settings.root else None)`.
  Tests keep injecting a service, so they never touch `/var/db` or `/var/run`.
- [ ] Run, then full suite.
- [ ] Commit: `feat(web): grants gate the same mutation path humans use`

### Task 9: `--mcp-session` handshake and stdin EOF

**Files:**
- Modify: `ntfsmnt/web/app.py` (`run_server`, new `_watch_stdin`), `ntfsmnt/cli.py` (`web` parser)
- Test: `tests/test_web_cli.py`

**Interfaces:**
```python
def _watch_stdin(stream, request_exit: Callable[[], None]) -> threading.Thread: ...
```
`run_server` in MCP mode prints exactly one line:
```json
{"ntfsmnt":"mcp-session","port":54321,"agent_token":"..."}
```
and does not open a browser.

- [ ] Tests:
  - `test_mcp_session_prints_one_json_line` — fake `uvicorn.Server`, capture stdout, assert keys and no URL.
  - `test_mcp_session_never_prints_the_human_token`.
  - `test_normal_mode_still_prints_the_url_line`.
  - `test_watch_stdin_calls_exit_on_eof` — pipe, write nothing, close, join with timeout.
  - `test_web_parser_accepts_mcp_session_flags`.
- [ ] Run; expect failure.
- [ ] Implement. `server.should_exit = True` from the watcher; the server object must be constructed before `run()` (small refactor). In MCP mode, append `mcp.session.start` to the audit log before printing the handshake.
- [ ] Run, then full suite.
- [ ] Commit: `feat(web): mcp-session handshake and exit on stdin EOF`

## Phase 3 — MCP adapter

### Task 10: Controller client

**Files:**
- Create: `ntfsmnt/mcp_client.py`
- Test: `tests/test_mcp_client.py`

**Interfaces:**
```python
class ControllerError(Exception):
    code: str; message: str; next_step: str | None

class Controller:
    def __init__(self, base_url: str, agent_token: str, *,
                 http: Callable[..., Any] = urlopen, timeout: float = 60.0): ...
    def get(self, path: str) -> dict: ...
    def post(self, path: str, body: dict | None = None,
             extra: dict[str, str] | None = None) -> dict: ...
    def delete(self, path: str) -> dict: ...

@dataclass
class ControllerSession:
    process: subprocess.Popen
    controller: Controller
    def close(self, *, grace: float = 5.0) -> None: ...

def start_controller(*, uid: int, gid: int,
                     cli: str = "/usr/local/libexec/ntfsmnt/bin/ntfsmnt",
                     sudo: str = "/usr/bin/sudo", timeout: float = 10.0,
                     popen: Callable[..., Any] = subprocess.Popen) -> ControllerSession: ...
```

- [ ] Tests with a fake `popen` and a fake HTTP callable:
  - `test_handshake_parses_the_json_line` — fake child writes the line, then blocks on stdin.
  - `test_handshake_timeout_terminates_only_its_own_child` — `close(grace=0)` then `terminate`, never `kill`.
  - `test_close_closes_stdin_first_so_the_controller_exits_on_eof`.
  - `test_controller_sets_bearer_header_and_origin`.
  - `test_http_error_maps_to_controller_error` — a 403 body `{"error":{"code":"grant_required"}}` becomes `ControllerError("grant_required", ...)`.
- [ ] Run; expect failure.
- [ ] Implement with `urllib.request.Request`; parse JSON; never log the token. `start_controller` argv is exactly:
  `[sudo, "-n", cli, "web", "--mcp-session", "--port", "0", "--owner", f"{uid}:{gid}", "--exit-on-stdin-eof"]`.
- [ ] Run, then full suite.
- [ ] Commit: `feat(mcp): controller client with a JSON handshake and SIGTERM-only shutdown`

### Task 11: Action tools

**Files:**
- Modify: `ntfsmnt/mcp.py`
- Test: `tests/test_mcp_actions.py` (keep `tests/test_mcp.py` green)

**Interfaces:**
```python
SUPPORTED_TOOLS = ("list_volumes", "get_status", "run_doctor",
                   "request_grant", "mount_volume", "unmount_volume",
                   "eject_volume", "list_grants", "revoke_grant")

def call_tool(name, arguments, *, collect=_discover,
              controller=None, client_name: str = "mcp") -> dict: ...
def serve(stdin, stdout, *, controller_factory: Callable[[], Controller] | None = None) -> int: ...
```

- [ ] Tests:
  - `test_tool_schemas_cover_nine_tools_and_mark_destructive_ones_absent`.
  - `test_initialize_instructions_mention_grants_and_approval` — replace "Read-only tools … Mounting is available through the web UI".
  - `test_initialize_records_the_client_name` — `clientInfo.name` reaches `request_grant` as `client`.
  - `test_request_grant_polls_until_approved` — fake controller returns pending then approved; result is `{"status":"approved","grant_id":...}` and never contains `approval_url`.
  - `test_request_grant_timeout_returns_timeout`.
  - `test_mount_sends_the_grant_header`.
  - `test_controller_error_becomes_isError_true_with_a_code`.
  - `test_read_tools_never_start_the_controller` — factory raises if called.
  - `test_action_tool_without_a_controller_is_a_structured_error`.
  - `test_serve_ignores_notifications` (regression for the existing protocol).
- [ ] Run; expect failure.
- [ ] Implement; controller starts lazily on the first action/grant tool and is closed when `serve` returns.
- [ ] Run, then full suite.
- [ ] Commit: `feat(mcp): six action tools behind the grant contract`

### Task 12: Approval opener and Touch ID fallback

**Files:**
- Modify: `ntfsmnt/mcp_client.py`, `ntfsmnt/mcp.py`, `ntfsmnt/cli.py` (`mcp --approve`, `mcp --no-browser`)
- Test: `tests/test_mcp_actions.py`
- Create: `app/Approve.swift`
- Modify: `scripts/build-app.sh`

**Interfaces:**
```python
def open_approval(url: str, *, mode: str = "browser",
                  helper: str | None = None,
                  request: dict | None = None,
                  run: Callable[..., Any] = subprocess.run,
                  open_url: Callable[[str], bool] = webbrowser.open) -> bool: ...
```
Helper lookup order: `$NTFSMNT_APPROVE_HELPER`, `/Applications/ntfsmnt.app/Contents/MacOS/ntfsmnt-approve`, `~/Applications/ntfsmnt.app/...`.

- [ ] Tests:
  - `test_browser_mode_calls_webbrowser_and_returns_true`.
  - `test_touchid_mode_pipes_the_nonce_on_stdin_not_argv`.
  - `test_missing_helper_falls_back_to_the_browser_once`.
  - `test_helper_exit_2_falls_back`, `test_helper_exit_0_does_not`.
  - `test_no_browser_mode_prints_the_url_to_stderr` (capsys, stdout stays empty).
- [ ] Run; expect failure.
- [ ] Implement `app/Approve.swift`: read JSON `{url, request_id, nonce, volume, actions}` from stdin, `LAContext.evaluatePolicy(.deviceOwnerAuthentication)`, POST approve/deny with `X-NTFSMNT-APPROVAL`, `X-NTFSMNT: 1`, `Origin`; exit 0 approved, 3 denied, 2 unavailable. Add it to `scripts/build-app.sh`'s `swiftc` invocation and the bundle's `Contents/MacOS`.
- [ ] Run, then full suite. Note in the plan: the Swift path has no automated test; manual check is in Task 17.
- [ ] Commit: `feat(mcp): browser approval with an optional Touch ID helper`

## Phase 4 — distribution, demo, docs

### Task 13: Demo image

**Files:**
- Create: `ntfsmnt/demo.py`
- Modify: `ntfsmnt/cli.py` (`demo`, `mcp --demo`)
- Test: `tests/test_demo.py`

**Interfaces:**
```python
DEMO_URL: str
DEMO_SHA256: str
def cache_path() -> Path: ...                    # ~/Library/Caches/ntfsmnt/demo.img
def ensure_image(*, fetch=None, path: Path | None = None) -> Path: ...
def attach_image(path: Path, *, run=real_run) -> str: ...   # returns /dev/diskN
```

- [ ] Tests:
  - `test_existing_file_with_matching_hash_is_not_downloaded`.
  - `test_hash_mismatch_deletes_and_raises` — no attach call.
  - `test_attach_uses_readonly_and_nomount` — argv contains `-readonly` and `-nomount`.
  - `test_attach_parses_the_device_from_hdiutil_output`.
  - `test_cli_demo_prints_the_device` and `test_cli_demo_json`.
  - `test_mcp_demo_attaches_before_serving` (fake attach + fake serve).
- [ ] Run; expect failure. Fill `DEMO_URL`/`DEMO_SHA256` only when the M4 asset is uploaded; until then the test pins a local fixture hash.
- [ ] Implement with a `.part` file, `urllib.request.urlopen`, `hashlib.sha256`, `os.replace`.
- [ ] Run, then full suite.
- [ ] Commit: `feat(demo): hash-pinned image download and read-only attach`

### Task 14: Registry manifest

**Files:**
- Create: `server.json`
- Modify: `pyproject.toml` only if hatchling would otherwise miss the file (it is repo metadata, not wheel content)
- Test: `tests/test_server_json.py`

- [ ] Tests:
  - `test_server_json_is_valid_and_version_matches`.
  - `test_package_is_pypi_stdio_running_mcp` — `command == "uvx"`, args end with `mcp`.
  - `test_no_extra_or_fuse_t_bundling_claims`.
  - `test_tool_list_matches_supported_tools` — parse `ntfsmnt.mcp.SUPPORTED_TOOLS` and assert the manifest's list is a subset that includes all action tools.
- [ ] Run; expect failure.
- [ ] Implement `server.json` with the official schema URL, one PyPI package entry, `stdio`.
- [ ] Run, then full suite.
- [ ] Commit: `feat: MCP registry manifest for the PyPI stdio server`

Submitting to the official registry and Smithery is a **human step** (accounts
and a published release); the plan stops at a manifest that a publish can use.

### Task 15: Truth updates

**Files:**
- Modify: `AGENTS.md` (the AI-native surfaces bullet and the module map), `docs/AGENT.md`, `README.md`, `site/llms.txt`, `docs/specs/2026-11-03-mcp-action-server.md` (status → approved), `docs/mcp-action-server.html` (regenerate with `scripts/render-spec.sh`)
- Test: `tests/test_packaging.py` / `tests/test_docs.py` if a doc assertion exists

- [ ] Replace `ntfsmnt mcp` "read-only … keep it that way" with the grant-scoped contract; list the nine tools; state the agent token and the one-time privileged install.
- [ ] Document `uvx ntfsmnt mcp`, `--approve touchid`, `--demo`, and the demo download in `docs/AGENT.md` and `README.md`.
- [ ] Add the new modules to the `AGENTS.md` layout and module-responsibility lists.
- [ ] Set the spec status to approved and run `./scripts/render-spec.sh`; confirm `git diff --exit-code docs/mcp-action-server.html` is clean after the regeneration (the committed page must match the source).
- [ ] Run `uv run pytest -q`; fix any doc-count assertion (`AGENTS.md` currently says 403 tests).
- [ ] Commit: `docs: ntfsmnt mcp is grant-scoped, not read-only`

### Task 16: Full verification

- [ ] `uv run pytest -q` — all tests pass.
- [ ] `cd npm && npm test`.
- [ ] `uv run ntfsmnt mcp` with no arguments echoes nothing extra on stdout; send `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` and confirm nine tools.
- [ ] `uv run ntfsmnt mcp --help` shows `--approve` and `--demo`.
- [ ] Read `AGENTS.md` and confirm no invariant is weaker than before.
- [ ] Commit any fixups; do not push.

## Manual acceptance (not automated)

Add to `tests/manual/acceptance.md`, run once on the real Mac:

1. `sudo ./scripts/install-privileged.sh` (already installed is fine).
2. `uvx ntfsmnt mcp --demo` in a client; `list_volumes` shows the demo image as unmounted.
3. Ask for a mount. The approval page opens; **Deny** → `grant_required` on retry.
4. Ask again; **Approve** → mount succeeds, probe passes, `mount.ok` appears in `/var/db/ntfsmnt/audit.jsonl`.
5. With the app installed, repeat with `--approve touchid`: prompt appears, deny → fallback does **not** auto-approve.
6. Kill the MCP client with `SIGKILL`; confirm no `ntfsmnt web` root process remains (`pgrep -f "ntfsmnt web"`).
7. Try `recover` and `remove_hiberfile` through the agent; both refuse with the manual command.

## Out of scope

- Daemon/Unix socket (Option B), remote transport, fleet control.
- exFAT/APFS backends; only the `Backend` seam is documented.
- `ntfsmnt stats --json` and telemetry reporting (M5 in the spec).
- WebAuthn on the approval page.
