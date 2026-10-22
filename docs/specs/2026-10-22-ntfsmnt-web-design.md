# ntfsmnt web — local visual UX for the CLI

**Status:** design, pending review
**Date:** 2026-10-22
**Builds on:** `2026-10-21-ntfs-mounter-design.md` (the CLI spec, binding)

## 1. Problem

The CLI is correct but opaque while you are working with a drive. You have to
remember subcommands, read terminal output, and re-run `status` after each step
to know what actually happened. A failed mount prints a classification
(`hibernated`, `dirty`, `busy`) but the next step lives in your head.

## 2. Goal

A local web page that shows every NTFS volume, its real state, and the one
action that makes sense next — plus a guided path out of the common failures.
It is a *view and a trigger*, not a second implementation.

Success criteria:

- State is correct at a glance, without reading a terminal.
- Every action on the page is one the CLI already supports, with the same
  safety rules.
- A failure tells you what happened and offers the next step, never a raw
  errno.
- `sudo ntfsmnt web` is the only new command you need to learn.

## 3. Non-goals

- A menu-bar app, daemon, or launch agent. The service lives for the session.
- Remote access. Loopback only.
- Replacing or duplicating CLI logic. The web layer calls the same modules.
- Auto-repair, auto-mount, auto-eject, or unattended operation.
- Persistent history. Activity is in-memory for the session.
- Multi-user sessions or per-user permissions.
- TLS. Loopback HTTP with a per-launch token.

## 4. Shape

```
sudo ntfsmnt web [--port N] [--open] [--owner uid:gid]
  → bind 127.0.0.1:<port>  (port 0 = OS-assigned; default 0)
  → print  http://127.0.0.1:<port>/?token=<32-byte urlsafe>
  → --open hands that URL to the default browser
```

`ntfsmnt web` is a subcommand of the existing CLI. Its implementation lives in
a new subpackage so the CLI's stdlib-only guarantee is untouched:

```
ntfsmnt/
  web/
    __init__.py
    app.py        FastAPI app, routes, auth middleware, lifespan
    service.py    action orchestration, operation lock, snapshot building
    schemas.py    pydantic models for state/errors/requests
    events.py     in-memory activity ring + SSE broadcaster
    page/         Jinja templates + static CSS/JS
```

FastAPI, uvicorn, and jinja2 live in an optional extra:

```toml
[project.optional-dependencies]
web = ["fastapi>=0.115", "uvicorn>=0.30", "jinja2>=3.1"]
```

`ntfsmnt web` imports `ntfsmnt.web.app` lazily. A missing extra prints an
install hint (`uv tool install 'ntfsmnt[web]'`) instead of a traceback. The
CLI's 153 existing tests run without the extra installed. The three web
dependencies are pinned by the lockfile and run as root, so upgrades are a
deliberate change, not a floating range resolved on install.

## 5. Process and privilege model

The service must already be root. It never escalates and never calls
`privileged.need_root`; non-root is a supported read-only mode, not an error.

At startup:

- Capture `SUDO_UID` / `SUDO_GID` into the session's owner pair. These are the
  ids passed to `mounter.mount(uid=, gid=)` and the ownership the write probe
  checks — exactly what the CLI does after its sudo re-exec.
- If `euid == 0` but `SUDO_UID` / `SUDO_GID` are unavailable, refuse to enable
  mutations unless `--owner uid:gid` is given. Mounting a volume root-owned is
  never the intent, and a silent fallback to `getuid()` would do exactly that.
- One uvicorn worker, no reload. The operation lock, activity ring, and SSE
  fan-out are in-process state; multiple workers would break all three.

Read-only mode (non-root, or root with unresolved owner):

- `/` renders normally with every volume and its state.
- A persistent banner explains the exact relaunch command.
- Every mutation endpoint returns `403 not_root` (or `owner_unresolved`).

Shutting the service down (`Ctrl-C`, or the process exiting) never unmounts
anything. Mounts belong to `ntfs-3g` and the kernel; the page is only a view and
a trigger, so closing it leaves the drives exactly as they were.

## 6. Security

The service has root and can destroy a filesystem, so the browser surface is
locked down even on loopback.

- **Bind**: `127.0.0.1` only. No `--host` flag.
- **Token**: 32 random bytes (`secrets.token_urlsafe(32)`), regenerated each
  launch, printed in the launch URL. `GET /?token=…` compares in constant time,
  sets the session cookie, and `303`s to `/` so the token leaves the address
  bar. A missing or wrong token is `401` with a one-line page: relaunch
  `ntfsmnt web`.
- **Cookie**: `ntfsmnt_session`, `HttpOnly`, `SameSite=Strict`, `Path=/`,
  `Secure` off (loopback HTTP), session-scoped; its value is a SHA-256 of the
  launch token, compared with `hmac.compare_digest`.
- **Host check**: only `127.0.0.1:<port>` and `localhost:<port>` are accepted;
  anything else is `421` (DNS-rebinding defence).
- **Mutations**: require the cookie, header `X-NTFSMNT: 1`, and an `Origin`
  exactly matching the bound origin. Missing/foreign origin, or a form content
  type, is `403`. The page sets the header globally with `hx-headers`.
  Read-only `GET`s need the cookie only.
- **Destructive confirmation**: the typed label is checked server-side before
  the request reaches the mounter, and the service appends `--confirmed`
  itself. `POST …/mount` with `{"remove_hiberfile": true}` and a missing or
  mismatched `confirm` is `400 confirmation_mismatch` with no side effects.
  The UI disabling the button is convenience, not the gate.
- **Logging**: request lines exclude query strings (the token) and request
  bodies. stderr captured from ntfs-3g is logged; the session token never is.
- **Untrusted strings**: volume labels, UUIDs, mount points, and ntfs-3g
  stderr are attacker-controlled (a USB stick chooses its own label). They are
  rendered only through Jinja autoescape — never `|safe`, never `innerHTML` —
  and a test feeds a label like `<img src=x onerror=alert(1)>` through every
  template. This matters more than usual: the page can mount disks as root, so
  an XSS here is a root action, not a defacement.

## 7. API

All JSON. `{id}` is the volume's UUID (`E69303DC-…`), or `dev-<basename>`
when `diskutil` reports none. It is an identity, not a slot: a page left open
across a replug gets `409 volume_changed` instead of acting on whatever now
occupies `disk4s1`. Every action also carries the `label` the user saw; the
server compares it with fresh discovery before touching anything.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | Full page |
| `GET` | `/api/v1/state` | Snapshot: host, doctor, operation, volumes |
| `GET` | `/api/v1/events` | SSE stream |
| `GET` | `/api/v1/activity` | Activity ring (seed on load) |
| `GET` | `/partials/volumes` | Rendered list + detail fragment (initial paint, fallback refresh) |
| `POST` | `/api/v1/volumes/{id}/mount` | Mount read-write |
| `POST` | `/api/v1/volumes/{id}/unmount` | Unmount, restore read-only |
| `POST` | `/api/v1/volumes/{id}/eject` | Unmount and eject parent disk |

### State snapshot

```json
{
  "generated_at": "2026-10-22T14:02:11Z",
  "host": {
    "root": true,
    "read_only": false,
    "owner": {"uid": 501, "gid": 20},
    "fuse_t": true,
    "ntfs_3g": true
  },
  "doctor": {
    "status": "warning",
    "checked_at": "…",
    "findings": [
      {"severity": "warning", "check": "device-access",
       "message": "HD1 device is busy because the volume is mounted.",
       "fix": null}
    ]
  },
  "operation": null,
  "volumes": [
    {
      "id": "E69303DC-…",
      "device": "/dev/disk4s1",
      "label": "HD1",
      "uuid": "E69303DC-…",
      "size": 1000204886016,
      "fs": "ntfs",
      "state": "ro",
      "mount_point": "/Volumes/HD1",
      "driver": null,
      "pid": null,
      "flags": {"hibernated": false, "dirty": false},
      "actions": [
        {"name": "mount", "enabled": true, "reason": null},
        {"name": "unmount", "enabled": true, "reason": null},
        {"name": "eject", "enabled": true, "reason": null}
      ],
      "options": [
        {"name": "recover", "enabled": true, "guarded": false, "reason": null},
        {"name": "remove_hiberfile", "enabled": true, "guarded": true,
         "reason": "Windows hibernation state will be discarded."}
      ]
    }
  ]
}
```

`actions` and `options` are computed server-side from the same predicates the
CLI uses, so the page never re-derives policy: `actions` are the buttons,
`options` are the Advanced checkboxes (`--recover`, `--remove-hiberfile`).
`state` is `rw`, `ro`, `unmounted`, or `error`.
`owner` is null in read-only mode. `uuid` is null when the fallback
`dev-<basename>` id is used. `doctor` is the last result, not a live probe.
`operation` is either `null` or
`{"id": "op-7", "action": "mount", "volume": "HD1", "started_at": "…"}`.

`GET /api/v1/activity` returns
`{"entries": [{"id": 41, "at": "…", "action": "mount", "volume": "HD1",
"ok": true, "summary": "writable", "detail": null}]}`, newest first.

### Actions

Request body (all fields optional except where guarded):

```json
{"label": "HD1", "recover": false, "remove_hiberfile": false, "confirm": "HD1"}
```

`label` is required on every action: it is the identity check the user implied
by clicking that row, and the server refuses a mismatch rather than trusting the
path.

Lifecycle for every action:

1. Acquire the single operation lock; else `409 operation_in_progress`.
2. Re-discover volumes fresh. Resolve `{id}` against that snapshot; if no
   volume has that identity, `404 volume_not_found`; if more than one does,
   `409 ambiguous_volume`. If the identity still exists but its label or size
   no longer match the request, `409 volume_changed` — never act on a device
   slot that changed underneath the page.
3. Validate the request against the fresh volume: action enabled, flags
   permitted, typed label exact.
4. Run the same `mounter` / `unmounter` call the CLI makes, with the captured
   owner ids and the same injected runners.
5. Append an activity entry, broadcast `activity` + `operation` + `state`.
6. Release the lock.

Response:

```json
{"result": {"ok": true, "message": "HD1 is writable.",
            "restored": null},
 "volume": { …updated volume… },
 "operation": null}
```

For a failed mount, `ok` is false, `restored` states whether the read-only
mount was confirmed back, and `error` carries the structured failure. A restore
that could not be confirmed is `restored: false` and the UI shows a red
blocker — it never reports a clean failure.

Idempotency mirrors the CLI: mounting an already-writable volume is `200` with
a no-op message; unmount/eject of an unmounted volume is `409 not_mounted`.

### Errors

```json
{"error": {
  "code": "blocked_hibernated",
  "message": "HD1 is hibernated by Windows.",
  "next_step": "Mount with hiberfile removal, or shut Windows down fully.",
  "volume": "HD1",
  "detail": "ntfs-3g: Volume is hibernated…"
}}
```

| Code | HTTP | Meaning |
|---|---|---|
| `unauthorized` | 401 | Missing/wrong token |
| `forbidden_origin` | 403 | Bad/missing Origin or `X-NTFSMNT` |
| `not_root` | 403 | Mutations disabled without root |
| `owner_unresolved` | 403 | Root without `SUDO_UID`/`--owner` |
| `volume_not_found` | 404 | The UUID/device no longer exists |
| `volume_changed` | 409 | That identity now names a different label/size |
| `ambiguous_label` | 409 | Two volumes share the label; candidates listed |
| `ambiguous_volume` | 409 | Two volumes share the identity (cloned UUID) |
| `operation_in_progress` | 409 | Another action holds the lock |
| `not_mounted` | 409 | Unmount/eject with nothing mounted |
| `doctor_blocker` | 409 | FUSE-T / ntfs-3g / device access missing |
| `blocked_hibernated` | 422 | Offer guarded mount |
| `blocked_dirty` | 422 | Offer `--recover` mount |
| `busy` | 409 | Mount point busy; names the blocker |
| `permission_denied` | 403 | Root but no Full Disk Access |
| `confirmation_mismatch` | 400 | Typed label wrong or absent |
| `action_failed` | 500 | The CLI's message fits no more specific code |
| `internal` | 500 | Unexpected; detail in server log only |

`next_step` is always present for failures that have one, and the UI turns a
known `next_step` into the matching button (`blocked_hibernated` → inline
confirm; `blocked_dirty` → **Mount with journal recovery**). Unknown steps
render as selectable text.

### SSE

`GET /api/v1/events` emits named events:

- `state` — full snapshot, on connect and after every action
- `activity` — one entry
- `operation` — started/finished, carrying the operation id
- `doctor` — findings after a check
- `ping` — every 20 s to keep proxies and the browser honest

Each event carries `id: <monotonic int>`. On reconnect, `Last-Event-ID` replays
from the 100-entry ring; if the id is older than the ring, the server sends a
full `state` instead. The client also re-fetches `/api/v1/state` on
`visibilitychange` and after `EventSource` reconnect.

## 8. Freshness

- **After an action**: synchronous rediscovery, then `state` broadcast. No
  polling race.
- **Reconcile tick**: rediscover every 5 s *only while at least one SSE client
  is connected*, and skip the broadcast while the operation lock is held —
  otherwise a snapshot taken between unmount and mount can publish a torn
  state. Broadcast only when the snapshot differs from the last one, so an idle
  page is quiet.
- **Doctor**: on page load, on **Re-check**, and after a failed action. Never
  on the reconcile tick — `ntfs-3g.probe` is not free.
- **Device changes**: picked up by the reconcile tick; no filesystem watcher in
  the MVP.

## 9. Visual design

**Layout C — master–detail.** Compact volume list on the left (38%, min 260 px,
max 360 px), selected volume on the right. A selected volume's actions, advanced
options, inline confirm, and its own log live in the detail panel. On first
load, select the first volume that needs attention, else the first volume.

**Style C — editorial light.** Warm paper `#FAF8F5`, ink `#2C2C2C`, panel
`#FFFFFF`, border `#E8E3DA`, muted `#6F6A63`, log surface `#F4F1EC`. Accent
blue `#2C5EA8` for primary actions, amber `#B07D00` for read-only and warnings,
green `#2F7D4F` for writable, red `#B3261E` for blockers. Serif headings
(Iowan Old Style / Georgia), system sans body, monospace log (SF Mono).
Generous whitespace, hairline rules, no cards-in-cards, no shadows except a
2 px focus ring. Motion capped at 150 ms and disabled under
`prefers-reduced-motion`.

**Header**: wordmark `ntfsmnt`, root/read-only badge, doctor status
(`doctor ✓` or `doctor: 1 warning`), and a quiet "live" indicator that dims
when SSE disconnects.

**Volume row**: label, state chip, device + mount point on a second line.
Selected row is marked by a left rule and a paper-tinted background, not colour
alone.

**State chips** — text first, colour second:

| State | Chip |
|---|---|
| writable | green dot + `writable` |
| read-only | amber dot + `read-only` |
| unmounted | hollow grey dot + `unmounted` |
| error | red dot + `error` |

**Detail panel**: volume title + chip, metadata line (`/dev/disk4s1 · 1 TB ·
UUID … · mounted at /Volumes/HD1 · FUSE-T, pid 4242`), primary actions
(`Make writable` / `Unmount` / `Eject`), then a collapsed **Advanced** row for
`recover` and `remove hiberfile`. Disabled actions carry their reason inline,
not in a tooltip.

**Inline destructive confirm (Option D, simple).** Guarded actions expand in
place, no overlay:

- amber warning block naming the exact volume and what changes;
- one field, visibly labelled *"Type HD1 to confirm"*;
- the exact command preview in small monospace,
  `sudo ntfsmnt mount HD1 --remove-hiberfile --confirmed`;
- **Confirm** disabled until the text matches exactly; **Cancel** as a text
  link; `Esc` also cancels;
- focus moves into the field on expand and returns to the triggering button on
  cancel.

**Doctor banner**: quiet one-liner when healthy. Amber for advisory findings
(e.g. device busy because mounted) — actions stay enabled. Red for blockers
(FUSE-T or ntfs-3g missing) — mount actions disabled, fix shown inline. Every
finding exposes its fix as selectable text with a copy button. No auto-repair.

**Activity log**: below the actions in the detail panel, newest first, ring of
100. Each line has time, verb, label, and outcome; failures expand to stderr
and the next step. Toggle **This volume / All**. Seeded from
`/api/v1/activity`, then fed by SSE.

**Empty and degraded states**

- No NTFS volumes: *"No NTFS volumes detected."* with a rescan hint.
- Not root: persistent banner *"Read-only mode — restart with
  `sudo ntfsmnt web` to enable actions."* Buttons disabled, state visible.
- SSE lost: *"Lost connection to ntfsmnt. Retrying…"* plus a manual retry;
  htmx actions still work.
- `409 operation_in_progress`: inline notice, buttons disabled until the
  `operation` event clears.

**Responsive**: ≥900 px is the master–detail layout. Below 900 px the list is
full width; choosing a volume replaces it with the detail view and a
**← All drives** link. No horizontal scroll and no second layout to maintain.

**Accessibility**

- Volume rows are real `<button>`s; ↑/↓ move selection, `Enter` opens, `Esc`
  backs out of the confirm.
- Focus is visible (2 px ring) and managed on expand/cancel.
- Action results announce in an `aria-live="polite"` region; doctor blockers
  use `role="alert"`.
- Contrast ≥4.5:1; state is never colour-only; the log is real text.
- The form uses real `<label>` elements; errors are linked with
  `aria-describedby`.

## 10. Safety invariants preserved

These are copied from the CLI spec and must not regress (§7 of the CLI spec):

1. No `osascript`, no GUI privilege dialogs, no escalation from the service.
2. Never run `ntfsfix`, `ntfsresize`, `mkntfs`, or `ntfswipe`.
3. Never `SIGKILL`; `unmounter` keeps its single `SIGTERM`.
4. `remove_hiberfile` requires the typed label, checked before the mounter is
   called and before `--confirmed` is appended.
5. A failed mount restores the read-only mount, and the response reports
   whether the restore was confirmed.
6. Duplicate labels are refused with candidates; never guessed.
7. An entry's own source is its identity; a relabelled mount is refused with
   its path and label.
8. No write without the probe and the ownership check.
9. Hibernated and dirty volumes are refused unless explicitly opted in.
10. All external effects go through the existing injected callables, so web
    tests never touch `/Volumes` or a real device.

## 11. Testing

**Service unit tests** reuse the CLI's injection points (`run`,
`read_mount_table`, probe, sleep).

- typed-label mismatch refuses before any mounter call
- an action whose `{id}` now names a different label or size returns
  `volume_changed` and never calls the mounter
- second concurrent action returns `409` and the first still completes
- fresh discovery is used per action; a vanished id returns `404`
- a reconcile snapshot taken during a held lock is not broadcast
- error classification maps to the table in §7
- failed mount surfaces `restored` truthfully, including `false`

**API tests** with FastAPI `TestClient`:

- token exchange sets the cookie and redirects; wrong token is `401`
- mutations without `X-NTFSMNT` or with a foreign `Origin` are `403`
- `Host` other than loopback is `421`
- non-root snapshot renders `read_only`; mutations are `403`
- unknown volume `404`, concurrent `409`, unmounted unmount `409`
- `/api/v1/state` shape validates against `schemas.py`
- `/partials/volumes` renders chips and disabled states

**Template tests** render Jinja partials against fixture snapshots and assert
the promised accessibility hooks (`aria-live`, `role="alert"`, labels), the
inline confirm only for guarded actions, and the command preview text. A
hostile label (`<img src=x onerror=…>`) and hostile stderr detail must appear
escaped in every template that renders them.

**SSE tests** assert an event after an action, `Last-Event-ID` replay, and the
full-state fallback when the id is out of range.

**Manual acceptance** (new section in `tests/manual/acceptance.md`), run on
real hardware as root from an FDA terminal:

1. `sudo ntfsmnt web` prints a URL; open it; every volume and state chip
   matches `ntfsmnt list`.
2. Mount HD1 from the page; write a file in Finder; state flips to writable
   without a manual refresh; the log records it.
3. Unmount; read-only mount returns and the chip flips back.
4. Eject; the volume disappears after the reconcile tick.
5. Guarded action on a hibernated volume: wrong label keeps Confirm disabled
   and a hand-crafted POST is rejected; correct label mounts with hiberfile
   removal.
6. Non-root launch: banner shows, buttons disabled, POST returns `403`.
7. Close the tab; the service keeps running until `Ctrl-C` and then exits
   cleanly.

CI runs everything except the manual section, without root or a device.
Web tests `pytest.importorskip("fastapi")`, so `uv run pytest -q` stays green
when the extra is absent. Root-only tests skip when `euid != 0`. The existing
153 CLI tests remain green and runnable without the `web` extra.

## 12. Acceptance criteria

1. `sudo ntfsmnt web --open` opens a page showing HD1/HD2 and the USB NTFS
   volumes with correct `read-only` / `unmounted` chips.
2. Making HD1 writable succeeds from the page, and the chip updates without a
   reload.
3. Unmount restores read-only; the page reflects it and the log says so.
4. A hibernated volume is refused by default; the offered guarded action
   requires the exact label, and a request without it never reaches ntfs-3g.
5. A failed mount reports whether read-only was restored; a failed restore is
   a red blocker, not a success.
6. Non-root launch is usable read-only and refuses every mutation.
7. Wrong token, foreign origin, and non-loopback `Host` are rejected.
8. A second action during one in flight returns `409` and leaves the first
   alone.
9. Below 900 px the page is usable without horizontal scroll, and the full
   flow works by keyboard alone.
10. `uv run pytest -q` is green without the `web` extra; `uv sync --extra web`
    installs the service; the CLI remains stdlib-only at runtime.
11. A page held open across a drive swap refuses the action with
    `volume_changed` instead of mounting whatever now occupies the old slot.

## 13. Open risks

- **Long-lived root HTTP process.** The blast radius is the local disk. The
  token, origin check, loopback bind, and typed-label gate are the mitigations;
  the service is still root, and that is stated in the launch output.
- **Token in shell and browser history.** The launch URL is printed, so the
  token lands in terminal scrollback and browser history. It is regenerated
  every launch and only opens a loopback page for the session, so this is
  accepted rather than papered over.
- **Reconcile cost.** `diskutil list -plist` every 5 s per connected page. Fine
  for a handful of drives; if it proves noisy, back off to 10 s or trigger on
  window focus only.
- **Device identity across replugs.** Fixed in the design: the API id is the
  volume UUID and every action re-verifies UUID + label + size fresh, so a
  replug produces `volume_changed` rather than a mount on the wrong drive.
  Residual: two cloned volumes sharing a UUID and label fall back to
  `dev-<basename>`; that is the same ambiguity the CLI already refuses rather
  than guesses.
- **Untrusted labels and stderr.** Attacker-controlled text reaches every
  template. Mitigated by autoescape-only rendering and an explicit hostile-label
  test; an accidental `|safe` is the failure mode to watch in review.
- **SSE through a sleeping laptop.** Reconnect plus `visibilitychange` refetch
  covers the common case; a missed event window is corrected by the next full
  `state`.
- **Single process.** No multi-worker deployment. Intentional: the lock, ring,
  and fan-out are in memory.
