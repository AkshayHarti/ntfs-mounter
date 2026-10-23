# AGENTS.md — ntfsmnt

Repo-local agent rules for `repos/ntfs-mounter`. Read before editing this repo.

## Stack

- Python ≥3.12, uv-managed.
- macOS-only CLI; stdlib-only runtime dependencies.
- FUSE-T 1.2.9 at `/usr/local/lib/libfuse-t.dylib`.
- ntfs-3g 2022.10.3 from `macos-fuse-t/ntfs-3g` at `/usr/local/bin/ntfs-3g`,
  linked against `libfuse-t`.

## Commands

- Install deps: `uv sync` (add `--extra web` for the browser UI and its tests)
- Test: `uv run pytest -q` (395 tests; without `--extra web` the FastAPI/Jinja
  API, page, and schema tests skip, while the service, models, and config tests
  run)
- Prerequisite check: `./scripts/setup.sh --check` (read-only, exits 0 anywhere)
- Prerequisites install: `./scripts/setup.sh` (FUSE-T cask + FUSE-T-linked ntfs-3g)
- Release: see `docs/RELEASING.md`; version lives only in `ntfsmnt/__init__.py`
- App build: `bash scripts/build-app.sh` (unsigned bundle in `dist/`)
- App install: `bash scripts/install-app.sh [--user] [--launch]`
- Privileged setup: `sudo ./scripts/install-privileged.sh` (`--check` read-only,
  exits 0 anywhere; `--uninstall` removes the rule before the venv)
- npm tests: `cd npm && npm test` (also run by CI)
- Site preview: `python3 -m http.server -d site` then http://localhost:8000
- Homebrew: `docs/HOMEBREW.md` (formulas + local-tap test commands)
- Manual acceptance: `tests/manual/acceptance.md` (real hardware, root, Full
  Disk Access terminal)
- Build/run: `uv run ntfsmnt ...`

## Layout

```
ntfsmnt/
  __init__.py      package marker
  __main__.py      python -m ntfsmnt entry point
  cli.py           argument parsing, command dispatch, JSON rendering
  mcp.py           read-only MCP server over stdio (`ntfsmnt mcp`)
  agent/           agentic UI core: OpenUI Lang parser/validator, AG-UI
                   events, optional LLM adapter, deterministic planner
  diskutil.py      plist parsing for diskutil list/info
  mounttable.py    parsing of `mount` output, including fuse-t: lines
  identity.py      corroborate a fuse-t: mount with its serving ntfs-3g process
  paths.py         ntfs-3g/probe resolution: NTFSMNT_NTFS3G override, FUSE-T
                   prefixes (/usr/local, /opt/homebrew), then PATH
  volumes.py       Volume model, build_volumes, resolve by label/uuid/device/mount
  options.py       ntfs-3g argument assembly and stderr classification
  doctor.py        environment checks with actionable fixes
  mounter.py       mount orchestration, competitor eviction, write probe
  unmounter.py     safe unmount, SIGTERM fallback, eject
  privileged.py    single privilege-escalation path: sudo re-exec via need_root
  models.py        Device dataclass
  web/
    app.py         FastAPI routes, auth boundary, middleware, SSE stream
    service.py     snapshots, doctor refresh, mount/unmount/eject orchestration
    events.py      EventBus (SSE fanout + replay ring) and the activity log
    config.py      Settings: token, cookie, hosts, origins, owner, port
    schemas.py     pydantic models for JSON responses and requests
    models.py      frozen dataclasses shared by service and schemas
    page/          index.html + fragments, app.css, app.js, vendored htmx
                   agent.html + agent.js + agent.css (the agentic surface)

tests/
  test_*.py        unit tests with injected runners
  test_packaging.py    pyproject metadata, licence, single-source version
  test_setup_script.py read-only checks of scripts/setup.sh
  test_paths.py    ntfs-3g resolution: override, candidates, PATH, probe
  test_install_privileged.py  static checks of the sudoers/venv installer
  test_npm_package.py  npm version sync, bin, no deps, workflow
  web_helpers.py   shared builders for the web tests
  web_render.py    Jinja template rendering helpers for the web tests
  fixtures/        captured diskutil / mount output
  manual/
    acceptance.md  human acceptance procedure (HD1 only)
    fuset-mount-test.sh  older root-only reference probe (verbatim)

scripts/
  setup.sh         idempotent FUSE-T + ntfs-3g installer, `--check` is read-only
  build-app.sh     compile + assemble + ad-hoc-sign the menu-bar app
  install-app.sh   build + copy the app to /Applications (or ~/Applications)
  install-privileged.sh  root-owned CLI + narrow NOPASSWD rule for `web` only
  sign-app.sh      Developer ID signing + notarization + stapling (needs notarytool)
  ntfsmnt-dryrun.zsh  older read-only environment report

Formula/
  ntfs-3g-fuse-t.rb  FUSE-T-linked ntfs-3g formula (`conflicts_with "ntfs-3g-mac"`)
  ntfsmnt.rb         virtualenv formula of the PyPI sdist incl. the web extra

app/               menu-bar companion app source (Info.plist + Swift)
site/              static GitHub Pages site (no JS, no build step)
npm/               dependency-free proxy package (`npx ntfsmnt`) + node:test suite

docs/
  specs/           design specs, one per feature
  plans/           implementation plans paired with the specs
  RELEASING.md     pending publisher, version bump, tag, rollback
  HOMEBREW.md      the two formulas and how they differ from setup.sh
  APP.md           what the menu-bar app does, unsigned run, notarization
  NPM.md           the proxy package, trusted publishing, version sync
  SITE.md          the Pages site, local preview, enablement
```

## Module responsibilities

- `diskutil.py` / `mounttable.py` — parse live system state.
- `volumes.py` — combine devices and mounts; determine `rw`/`ro`/`unmounted`.
- `identity.py` — decide whether a `fuse-t:` entry is *this* device's mount: only
  `fuse-t:/<label>` is a candidate, a unique label then settles it and `pgrep`
  corroborates; with a shared label the `ntfs-3g` process whose argv names the
  device must decide.
- `options.py` — build ntfs-3g options, classify failures (hibernated, dirty,
  busy, EPERM).
- `paths.py` — find the `ntfs-3g` binary and `ntfs-3g.probe`. Candidates beat
  PATH so a root process (sudo `env_reset`) can still resolve them; candidate
  order beats Homebrew's macFUSE-linked `ntfs-3g-mac`, which shares the binary
  name in `/opt/homebrew/bin`.
- `doctor.py` — check the host; hard failures are `fuse-t`, `ntfs-3g`, `root`,
  `device-access`.
- `mounter.py` — unmount Apple's mount, run ntfs-3g, evict Apple remounts,
  write-probe, restore read-only on any failure.
- `unmounter.py` — unmount FUSE-T and Apple mounts, optionally restore
  read-only, then eject parent disk.
- `privileged.py` — the only root-escalation path; `os.execvp` through `sudo`.
- `cli.py` — ties it together; resolves volume *before* privilege escalation,
  confirms destructive flags *before* escalation.
- `web/app.py` — the HTTP surface: token→cookie exchange, the session/
  `X-NTFSMNT`/Origin boundary, state and action routes, the SSE stream, and
  the loopback host plus response-header middleware.
- `web/auth.py` — the shared auth boundary (session cookie, mutation header,
  Origin) used by both pages.
- `web/agent.py` — the `/agent` routes: page, component library, and the
  AG-UI SSE run stream. It can refresh the doctor and propose actions; it
  never mounts, unmounts, or ejects.
- `web/service.py` — takes the same snapshot the CLI prints, decides which
  actions are enabled with the same predicates, and runs the CLI's
  `mounter`/`unmounter` calls under a single operation lock.
- `web/events.py` — in-process SSE bus with a 100-frame replay ring and the
  bounded activity log; publish is safe from worker threads.
- `web/config.py` — launch token, session cookie, allowed hosts/origins, the
  captured owner ids, and the read-only decision.
- `web/schemas.py` / `web/models.py` — the JSON contract; the service produces
  dataclasses and `schemas` validates/serializes them.

## Non-negotiable safety invariants

These are hard requirements. Do not weaken them in any change.

1. **No `osascript`.** Privilege escalation goes only through `privileged.py`
   → `sudo`. GUI password dialogs or `osascript -e 'do shell script ...'` are
   not used, because they lose the terminal's Full Disk Access attribution.
2. **Never auto-run repair tools.** Do not call `ntfsfix`, `ntfsresize`,
   `mkntfs`, or `ntfswipe`. Not as a fallback, not as a convenience.
3. **Never `kill -9`.** The only signal the unmounter sends is `SIGTERM`, once,
   after a normal unmount fails as busy.
4. **Destructive `--remove-hiberfile` requires the typed label.** It is the only
   flag that triggers `_confirm(label)`; the typed confirmation must happen
   *before* `need_root` so unprivileged tests never exec sudo. `--recover` is
   opt-in but not label-confirmed.
5. **Failed mounts restore the read-only mount.** `mounter.py` evicts Apple's
   mount, so every exit path must restore it. `_fail` and the `except
   BaseException` tail call `_restore_read_only`, which unmounts the mount point
   only when a fresh, best-effort table read shows a `fuse-t:` entry there (an
   unreadable table still attempts it), and restores last; it may claim
   "restored" only when the mount table confirms it.
6. **No guesses on duplicate labels.** `resolve()` raises `AmbiguousVolume` and
   the CLI lists candidates; never pick the first match. A shared label is
   resolved by the serving process; a `fuse-t:` mount is never evicted to make
   room, and an unanswerable `pgrep` refuses rather than guesses.
7. **An entry's own source is its identity.** Only `fuse-t:/<label>` can be this
   volume's mount; an entry naming another label is another volume's and is
   never classified or unmounted for this one. A `fuse-t:` entry sitting at the
   snapshot's own path that no label can claim is the relabelled case: `unmount`
   refuses with the path and the label the entry carries, and `eject` counts it
   as mounted (fail closed) rather than ejecting a disk under a live mount.
8. **No write without a probe.** `mounter.py` writes, reads back, and deletes
   `.ntfsmnt-probe`, then checks the mount point is owned by the invoking uid
   and gid, before reporting success.
9. **Hibernated and dirty volumes are refused by default.** `--recover` and
   `--remove-hiberfile` are opt-in only.
10. **All external effects go through injected callables.** Production passes
   `real_run`, `read_mount_table`, `time.sleep`, and the real probe; tests pass
   fakes. Every subprocess effect (`diskutil`, `ntfs-3g`, `mount`, `pgrep`,
   `kill`) goes through an injected callable (`run` or `read_mount_table`).
   `_ensure_mount_point` and `_write_probe` are injected or monkeypatched in
   tests, so no test writes to `/Volumes`.

When changing `mounter.py` or `unmounter.py`, first verify the failure-path
sequence still restores the read-only mount and does not add a `SIGKILL`.

## Web layer invariants

The browser UI (`sudo ntfsmnt web`) adds an HTTP boundary; it never bypasses the
CLI rules above. Do not weaken these either.

1. **Token → cookie, once.** The launch token arrives in the URL, is compared in
   constant time, and is exchanged for an `HttpOnly`, `SameSite=Strict` session
   cookie via a 303. Routes authenticate the cookie, never the URL.
2. **Every mutation needs `X-NTFSMNT: 1` and an allowed `Origin`.** htmx and
   the JSON client both send it; a cross-site form cannot. A request with a
   missing or foreign `Origin` is refused before any action code runs.
3. **Loopback `Host` only.** A request whose `Host` is not in the allow-list
   gets 421, so DNS rebinding cannot reach the session cookie.
4. **One operation at a time.** Every action holds a single non-blocking lock;
   a second request is `409 operation_in_progress`. The reconcile tick skips
   publishing while the lock is held, and re-checks it after the snapshot.
5. **Doctor freshness.** The doctor result is refreshed on page load, on
   Re-check, and after a failed action that touched the host — and the startup
   check runs before the first request. Purely bookkeeping failures
   (`volume_changed`, `confirmation_mismatch`, `operation_in_progress`, …) do
   not re-probe. It is never refreshed on the 5 s reconcile tick, and every
   refresh publishes a `doctor` event.
6. **Owner ids come from `SUDO_UID`/`SUDO_GID`** (or `--owner`), captured before
   the mount; the mount-point ownership check still uses the invoking user, not
   root.
7. **Read-only without root.** Without root (or without owner ids) every
   mutation is refused with a structured 403; state remains readable and the
   page says why. Nothing re-execs `sudo` on the user's behalf — with one
   sanctioned exception: the menu-bar app may start `web` through the narrow
   `NOPASSWD` rule described under "Privileged install invariants".

## Privileged install invariants

`scripts/install-privileged.sh` is the only thing allowed to grant root without
a prompt. Do not weaken these.

1. **Root-owned code only.** The `NOPASSWD` rule points at
   `/usr/local/libexec/ntfsmnt/bin/ntfsmnt`, a venv owned by `root:wheel` with
   no group/other write. A rule for a user-writable path is passwordless root
   for anyone who can edit it; the installer refuses one.
2. **The rule names `web` only.** Never widen it to `ALL`, to `mount`, or to a
   bare binary path. Mount/unmount/eject run inside the already-root server.
3. **Validate before installing.** `visudo -cf` on a temp file, then
   `install -m 0440 -o root -g wheel`; never edit `/etc/sudoers`.
4. **Uninstall removes the rule first**, then the venv, so there is no window
   where the grant outlives the code.
5. **No always-on service.** No LaunchDaemon, no login item; the server is a
   child of the app and dies with it. The token travels only over the stdout
   pipe, never a file.
6. **The app's `SIGKILL` is for its own server child only**, after `SIGTERM`
   and a 5 s wait. It must never target a mount, `ntfs-3g`, or any disk-related
   process; invariant 3 above still forbids `kill -9` there.

## AI-native surfaces

- `/agent` — the agentic UI. The agent composes OpenUI Lang from the fixed
  registry in `ntfsmnt/agent/library.py`, streamed over SSE as AG-UI events
  (`ntfsmnt/agent/events.py`, `ntfsmnt/web/agent.py`); the renderer is
  `ntfsmnt/web/page/agent.js`. Default planner is deterministic; `--agent-llm`
  enables an OpenAI-compatible model (validated output, deterministic
  fallback). The agent layer never mounts, unmounts, or ejects: generated
  buttons call the existing authenticated routes. Do not add an execution
  path to the agent, and do not render generated strings with `innerHTML`.
- `ntfsmnt mcp` — read-only MCP server on stdio (`ntfsmnt/mcp.py`). Its tools
  are `list_volumes`, `get_status`, `run_doctor`; there is deliberately no
  mount tool. Keep it that way.
- `site/llms.txt` — the agent-facing index of the docs.
- `docs/AGENT.md` — user-facing doc for both surfaces.

## Conventions

- Keep the README, this file, and `tests/manual/acceptance.md` honest about
  what is verified and what is not. Do not invent commands or promise behaviour
  the code does not implement.
- Do not hand-edit `/Users/akshayharti/workspace/REGISTRY.json`; the controller
  handles it.
