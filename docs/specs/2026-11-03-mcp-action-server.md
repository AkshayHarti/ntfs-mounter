# ntfsmnt as an agent — MCP action server (Option A) — design spec

**Date:** 2026-11-03
**Status:** draft for review — Option A approved as the starting shape
**Options board:** `docs/agent-options.html` (A/B/C comparison, trade-offs, wedge path)
**Plan:** to follow (`docs/plans/`)

## Problem

`ntfsmnt mcp` is deliberately read-only: it exposes `list_volumes`, `get_status`,
and `run_doctor`, and the module docstring says mounting "needs root and a human
who decides." That correctness is also the product limit. An agent can see a
disk but cannot finish the job — mount it, let the user write, unmount it — so
the user drops back to a terminal and the agent stops being useful at the exact
moment it could be.

The opportunity is not another mount command. It is a **consent primitive**: let
an agent act on disks inside a narrow, expiring, human-approved grant, and let
every action be auditable. Mounting is a commodity; a grant an agent can show
is the new thing.

## Audience

| Audience | What they need |
|---|---|
| Agent users (Claude Desktop, Cursor, Cline) | One install, a tool that finishes the task without a terminal |
| Developers / CI | Scriptable, deterministic, no GUI-only steps |
| Security-minded users | No standing root, no wildcard rule, no repair tools, a visible audit trail |
| Future format users (exFAT, APFS) | The same verbs, honest about which backend served them |

## Goals

- **G1** `ntfsmnt mcp` gains action tools: `mount_volume`, `unmount_volume`,
  `eject_volume`, plus `request_grant`, `list_grants`, `revoke_grant`.
- **G2** No action runs without a grant bound to one volume, a set of actions, and
  a TTL. Grants are approved by a human in the browser; nothing is auto-approved.
- **G3** The grant layer lives in `ntfsmnt/`, not in the MCP adapter, so Option B
  (daemon) is a packaging change: same interface, different host process.
- **G4** Distribution is one line (`uvx ntfsmnt mcp`), with an MCP registry
  manifest and a zero-hardware demo image.
- **G5** Every grant lifecycle event and every action is appended to a root-owned
  audit log; refusals are structured, not exceptions.

## Non-goals

- No daemon, LaunchDaemon, or always-on service in this spec (that is Option B).
- No remote transport, multi-tenant auth, or fleet control plane.
- No non-NTFS backends yet; the backend interface is designed, not built.
- No destructive actions through MCP: `--recover` and `--remove-hiberfile` are
  never grantable and return a manual command instead.
- No `osascript`, no repair tools, no `SIGKILL` — unchanged.

## Shape decision

Option A from `docs/agent-options.html`: a local stdio MCP server, one process
per client session, no installed service. Internally it spawns a **session
controller** — the existing root web server — via the rule the menu-bar app
already uses. That gives A the safety properties of B without B's install
friction.

Rejected alternatives:

| Alternative | Why not |
|---|---|
| Widen the sudoers rule to `mcp` | Adds a second root entry point; the rule stays `web` only |
| Run the MCP server as root directly | Every MCP client becomes root-capable; no approval boundary |
| Touch ID per action | Breaks the hero demo; still needs a controller for state |
| A real daemon now | Install friction kills the one-line demo; no demand proven |

## Architecture

```
MCP client (Claude / Cursor / CI)
   │  stdio, JSON-RPC 2.0
   ▼
ntfsmnt mcp  (user)                     ← thin adapter, no disk authority
   │  spawn: sudo -n <root venv>/bin/ntfsmnt web --mcp-session
   │         --port 0 --owner UID:GID --exit-on-stdin-eof
   │  reads one JSON line from the child's stdout:
   │         {"ntfsmnt":"mcp-session","port":N,"agent_token":"..."}
   ▼
ntfsmnt web  (root, session-scoped)     ← controller: lock, grants, audit
   │  existing mounter / unmounter / doctor
   ▼
already root → ntfs-3g → NTFS volume
```

- The controller is the existing `web` server. It already owns the operation
  lock, the snapshot, the doctor refresh, and the SSE/activity bus; MCP mode
  simply does not expose the dashboard.
- The adapter is disposable: it maps MCP tools to HTTP calls. It holds an
  **agent token**, which can read state, request grants, and use them, but
  cannot mint, approve, or deny one.
- **No human token in MCP mode.** The controller mints only an agent token;
  approval happens through a single-use nonce delivered to the human's browser.
  The normal menu-bar mode keeps today's single launch token unchanged.
- **stdout is the JSON-RPC channel.** The adapter never prints anything to its
  own stdout except protocol messages; diagnostics and any `--no-browser` URL go
  to stderr.

### Trust boundary

The adapter is code we ship; the model is the untrusted party. The boundary is
therefore a **tool surface with no approve verb** and a tool result that never
contains an approval URL. A compromised adapter is equivalent to the user's own
shell, which no in-process design can prevent — state that, do not pretend
otherwise.

### Controller lifetime

`--exit-on-stdin-eof` makes the controller shut down when the adapter closes the
child's stdin pipe. That covers the normal exit, an adapter crash, and a
`SIGKILL` of the adapter; without it a root controller could outlive its client.
This is a new, testable flag — the menu-bar app keeps its existing
SIGTERM-then-wait behaviour.

### Why not attach to an existing dashboard

A second root server with in-memory grants is harmless if mutations take a
**cross-process file lock** (`/var/run/ntfsmnt.lock`, root-owned, `flock`). The
spec adds that lock; the in-process lock alone is not enough once two servers
can exist. Lock contention returns `409 operation_in_progress` immediately — no
waiting, no queue. The lock file's contents are irrelevant, so a stale file is
harmless; the kernel releases `flock` on process death. A future `--attach`
discovery mechanism is out of scope.


## Tool contract

Read tools are unchanged and never need a grant.

| Tool | Input | Grant | Output |
|---|---|---|---|
| `list_volumes` | — | no | snapshot as today |
| `get_status` | `volume` | no | one volume + serving mount/pid |
| `run_doctor` | — | no | checks |
| `request_grant` | `volume`, `actions[]`, `ttl_seconds`, `reason` | no | `{request_id, status}` |
| `mount_volume` | `volume`, `grant_id` | yes | mount result + probe outcome |
| `unmount_volume` | `volume`, `grant_id` | yes | unmount result |
| `eject_volume` | `volume`, `grant_id` | yes | eject result |
| `list_grants` | — | no | active grants for this session |
| `revoke_grant` | `grant_id` | no | revoked / not found |

Rules:

- `actions` is a subset of `["mount", "unmount", "eject"]`. `recover` and
  `remove_hiberfile` are rejected at request time with `manual_required` and the
  exact CLI command. This is a hard, testable refusal.
- `ttl_seconds` is 60–1800 (default 900). One grant covers one volume, resolved
  to UUID + device + label at approval time and re-checked at use time.
- **A `mount` grant implies `unmount`** for the same volume and TTL: refusing
  the cleanup step would leave a disk mounted, which is strictly worse. A bare
  `unmount`/`eject` still needs its own grant.
- Every action tool returns a structured result; missing/expired/mismatched
  grants return `{code: "grant_required", request_hint: ...}`, never a stack
  trace and never a silent retry.
- Volume selectors accept the same forms as the CLI (label, UUID, device, mount
  point). Ambiguity refuses; it never picks the first match.
- At most **3 pending requests** per controller, deduplicated by
  `(volume, actions)`. More returns `too_many_requests`; this bounds approval
  spam from a looping model.

### Approval flow

1. Agent calls `request_grant`; the result is `{request_id, status: "pending"}`.
2. Adapter → controller `POST /api/v1/grants` with
   `Authorization: Bearer <agent token>`. The controller creates a pending
   request and returns `{request_id, approval_url, wait_seconds}`. The URL
   carries a **single-use approval nonce** — never a session token, never the
   agent token.
3. Adapter calls `webbrowser.open(approval_url)` (it runs as the user) and
   returns only `{request_id, status}` to the model. The model never sees the
   URL or any credential.
4. The page shows volume, actions, TTL, requesting client, and the agent's
   stated reason, with **Approve** / **Deny**. Destructive actions cannot appear.
   `POST /api/v1/grants/{request_id}/{approve|deny}` requires the nonce,
   `X-NTFSMNT: 1`, and an allowed `Origin`; **agent tokens are rejected on this
   route**, so no tool call can mint a grant. The page sets
   `Referrer-Policy: no-referrer` so the nonce cannot leak through a referrer.
5. Adapter polls `GET /api/v1/grants/{request_id}` with the agent token
   (100 ms backoff, capped at the request's `wait_seconds`, default 90, max 300)
   and returns `approved` / `denied` / `timeout`.

The browser click is the gate. Without it no grant exists, so no action can run.
`--no-browser` writes the approval URL to **stderr** (stdout is the JSON-RPC
channel) for a human to open manually; if nobody can, the request times out and
actions fail closed with `approval_unavailable`.


## Grant model

```json
{
  "id": "g_01J...",
  "volume": {"uuid": "...", "device": "/dev/disk4s1", "label": "HD1"},
  "actions": ["mount", "unmount"],
  "created_at": "2026-11-03T10:00:00Z",
  "expires_at": "2026-11-03T10:15:00Z",
  "reason": "copy footage from the camera card",
  "client": "Claude Desktop",
  "state": "active"
}
```

- **In-memory only**, owned by the controller process. No grant file to forge,
  edit, or leave behind. Session death invalidates every grant.
- **Identity**: a request carries `request_id`; approval mints a separate,
  unguessable `grant_id`. Only the `grant_id` is accepted by action tools.
- **Bound identity**: approval snapshots UUID + device + label; use re-resolves
  the live volume and refuses if the device or label changed (relabelled or
  swapped disks fail closed, matching invariant 7).
- **Time**: `expires_at` is checked on every use. Revocation is immediate.
- **One operation at a time** still applies: a grant authorizes, it does not
  queue. A second concurrent action gets `409 operation_in_progress`.
- **Destructive flags are not representable** in a grant.

## Privilege and auth boundary

- The adapter runs as the user and holds an **agent token**: read tools,
  `request_grant`, and grant-scoped mutations. It cannot mint, approve, or deny
  a grant.
- **MCP mode has no human token.** `web --mcp-session` mints only the agent
  token, prints one JSON line to the adapter's stdout pipe, and serves the
  approval page behind a single-use nonce. There is no session cookie to steal
  and no token in any URL. The normal menu-bar mode keeps today's single launch
  token and cookie exchange unchanged.
- The controller keeps the existing web invariants: loopback `Host` allow-list,
  `X-NTFSMNT` + `Origin` on mutations, constant-time token compare, and
  `HttpOnly`/`SameSite=Strict` where a cookie exists.
- The sudoers rule still names **`web` only**, pointing at root-owned code. No
  new NOPASSWD entry is added by this spec.
- Mutation routes gain one branch: an agent-token request must carry
  `X-NTFSMNT-GRANT: <grant_id>`; a human cookie request works as it does today.
  Mount, unmount, and eject have no typed confirmation today — only
  `--remove-hiberfile` does, and agents cannot request it.

Residual risks, stated rather than hidden:

- The approval nonce appears in the browser URL and in the adapter's memory for
  the life of one request. A same-user process that can read `ps` could observe
  it and approve that one request; it expires on use or with the request.
- A compromised adapter is equivalent to the user's own shell. The boundary
  protects against the *model*, not against arbitrary code running as the user.

## Audit

Append-only JSONL at `/var/db/ntfsmnt/audit.jsonl` (`root:wheel`, `0600`),
created by the installer (`/var/db/ntfsmnt` is `root:wheel`). One line per
event:

```json
{"ts":"2026-11-03T10:01:02Z","event":"grant.approved","grant":"g_01J...",
 "volume":"HD1","device":"/dev/disk4s1","actions":["mount"],"client":"Claude Desktop",
 "actor":"uid:501"}
{"ts":"2026-11-03T10:01:05Z","event":"mount.ok","grant":"g_01J...",
 "volume":"HD1","mount":"/Volumes/HD1","probe":"ok"}
{"ts":"2026-11-03T10:03:00Z","event":"grant.denied","request":"r_01J...",
 "reason":"user declined"}
```

Events: `mcp.session.start`, `grant.requested`, `grant.approved`,
`grant.denied`, `grant.expired`, `grant.revoked`, `action.refused` (with code),
`mount.ok`, `mount.fail`, `unmount.ok`, `unmount.fail`, `eject.ok`, `eject.fail`.
No file contents, no volume paths beyond the mount point.

**Audit failure is a safety decision, not an afterthought.** If the append fails,
`mount` and `eject` fail closed (`audit_unavailable`); `unmount` proceeds and
writes the failure to stderr, because leaving a volume mounted is worse than a
missing line. `ntfsmnt audit --json` is a follow-up, not this spec.

## Distribution

- **PyPI**: `uvx ntfsmnt mcp` runs the adapter. `mcp` is part of the base
  install (no extra) because the adapter is stdlib HTTP; the controller's
  `[web]` extra is installed once by the privileged installer, not by `uvx`.
  Keep `[web]` as the only extra unless the adapter needs a new dependency.
- **Homebrew**: same formula; `ntfsmnt mcp` needs no new deps.
- **npm**: stays a finder/proxy (`npx ntfsmnt mcp`), never bundles FUSE-T.
- **MCP registry**: `server.json` with one package entry, `stdio` transport,
  tool list, and a link to `docs/AGENT.md`. Submit to the official registry and
  Smithery in the same release.
- **Demo**: `ntfsmnt demo` attaches a small bundled NTFS image with `hdiutil`
  (exact flags verified in M4, not guessed here) so the hero flow runs with no
  external disk. Demo mode is explicitly labeled disposable.
- One-time privileged install remains: `sudo ./scripts/install-privileged.sh`
  (already required by the menu-bar app). The README's agent quickstart adds
  this step and nothing else.

## Format expansion — options

Decision now: keep tools format-agnostic and add a `Backend` seam; do not build
a second backend in this spec. Documented options for the next step:

| Option | Shape | NTFS | exFAT / APFS | Install weight | Verdict |
|---|---|---|---|---|---|
| **F1** Backend registry (recommended) | one binary, one MCP server, `Backend` protocol | ntfs-3g + FUSE-T | macOS native (`diskutil`, `mount`) | unchanged | Ship when a second format has users |
| F2 Capability packs | gateway + per-format packages | pack | pack | larger | Defer; only after F1 demand |
| F3 `diskutil` shim | shell out for everything non-NTFS | ntfs-3g | `diskutil` | smallest | Reject: no probe, no failure model |
| F4 Stay NTFS-only | no seam | ntfs-3g | — | none | Reject: forecloses the stated goal |

F1 design:

```python
class Backend(Protocol):
    name: str                     # "ntfs", "exfat", "apfs"
    can_handle(volume) -> bool
    mount(volume, options) -> MountResult
    unmount(volume) -> UnmountResult
    probe(mount_point) -> ProbeResult   # write/read/delete + ownership
    repair_policy: Literal["never"]     # stays "never" for every backend
```

- Tool names stay `mount_volume` / `unmount_volume`; the response includes
  `backend: "ntfs"` so an agent can explain what happened.
- The write probe, restore-on-failure, and no-repair rules are per-backend but
  identical in contract. exFAT has no journal; the dirty-volume refusal becomes
  "native mount failed → report, never repair."
- Grants bind to `(volume, backend)`. A format change on the same UUID is a
  different grant.
- Honest positioning: macOS already mounts exFAT and APFS read-write. The value
  there is orchestration, safe eject, diagnostics, and automation — not fixing
  a read-only mount. Marketing must not imply otherwise.
- Licensing: FUSE-T's non-commercial term applies to the NTFS backend only.
  Native exFAT/APFS keep a commercial path open.
- Naming options, to decide when F2/F1 lands: keep `ntfsmnt` (honest, narrow),
  rename to an umbrella (`volmnt`, `diskmnt`) and keep `ntfsmnt` as an alias, or
  ship a second package. Recommendation: keep `ntfsmnt` until a non-NTFS
  backend actually ships.

## Metrics

Instrumentation is part of the spec, not an afterthought:

| Signal | How | Target (initial) |
|---|---|---|
| Activation funnel | `mcp.session.start` → first read tool → `grant.requested` → approved → first mount | ≥ 40% of installs reach a mount in session 1 |
| Time to first mount | adapter start → first successful mount | p50 < 5 min from a clean Mac |
| Approval friction | request → approve latency; denial rate | p50 < 30 s; denial < 15% |
| Safety | MCP-initiated mutations with a valid grant / all MCP-initiated mutations | 100% |
| Refusals | `action.refused` by code | destructive via MCP: 0 attempts that succeed |
| Audit completeness | actions with a matching audit line (except `audit_unavailable` unmounts) | 100% |
| Distribution | registry installs, `uvx` downloads, stars | week-over-week growth, no absolute promise |

Telemetry stays local by default: counts in the audit log, no network calls. A
`ntfsmnt stats --json` reads the log; sharing is opt-in and documented.

## Milestones

1. **M1 — control layer** (`ntfsmnt/control/`): grants, `flock`, audit writer,
   agent-token scope, injected clock and store. Unit-tested without root.
2. **M2 — HTTP + approval UI**: `--mcp-session` and `--exit-on-stdin-eof`,
   grant routes, approval page with nonce, agent-token branch on mutation
   routes. Tested with `TestClient`.
3. **M3 — MCP adapter**: six new tools, structured errors, `--no-browser`
   (stderr) fallback, contract tests against a real controller subprocess.
4. **M4 — distribution**: `server.json`, registry submission, `ntfsmnt demo`
   image, installer creates `/var/db/ntfsmnt`, README/`docs/AGENT.md`
   quickstart.
5. **M5 — instrumentation** (after v1): `ntfsmnt stats --json`, activation
   funnel report.

**Doc truth:** `AGENTS.md` currently says `ntfsmnt mcp` is read-only and "keep
it that way." M3 must change that line to the grant-scoped contract (and
`docs/AGENT.md`, `README.md`, `site/llms.txt`) in the same PR. Shipping action
tools while the repo rules still claim read-only would be exactly the kind of
dishonesty `AGENTS.md` forbids.

## Risks

| Risk | Mitigation |
|---|---|
| Browser approval is awkward over SSH / CI | `--no-browser` writes the URL to stderr; fail closed without a click |
| Model invents grant ids | Structured `grant_required` with the request flow; no retries |
| Two controllers race | Cross-process `flock` on `/var/run/ntfsmnt.lock` |
| Root controller outlives the adapter | `--exit-on-stdin-eof`; pipe closes on crash or SIGKILL |
| Grant outlives intent | Short TTL (≤ 30 min), in-memory, visible revoke |
| Approval spam from a looping model | 3 pending requests max, deduplicated by volume+actions |
| Audit write fails mid-action | `mount`/`eject` refuse; `unmount` proceeds and logs to stderr |
| FUSE-T licence blocks paid distribution | Backend seam; native formats avoid FUSE-T |
| Name no longer fits exFAT | Decide at F1; keep `ntfsmnt` until then |

## Open questions

1. Do we want Touch ID as an optional approval method before v1 ships?
2. Does the demo image belong in the wheel (size) or download on first use?
3. When F1 lands, does `ntfsmnt` become the umbrella or stay the NTFS backend?

