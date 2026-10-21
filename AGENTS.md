# AGENTS.md — ntfsmnt

Repo-local agent rules for `repos/ntfs-mounter`. Read before editing this repo.

## Stack

- Python ≥3.12, uv-managed.
- macOS-only CLI; stdlib-only runtime dependencies.
- FUSE-T 1.2.9 at `/usr/local/lib/libfuse-t.dylib`.
- ntfs-3g 2022.10.3 from `macos-fuse-t/ntfs-3g` at `/usr/local/bin/ntfs-3g`,
  linked against `libfuse-t`.

## Commands

- Install deps: `uv sync`
- Test: `uv run pytest -q` (92 tests)
- Manual acceptance: `tests/manual/acceptance.md` (real hardware, root, Full
  Disk Access terminal)
- Build/run: `uv run ntfsmnt ...`

## Layout

```
ntfsmnt/
  __init__.py      package marker
  __main__.py      python -m ntfsmnt entry point
  cli.py           argument parsing, command dispatch, JSON rendering
  diskutil.py      plist parsing for diskutil list/info
  mounttable.py    parsing of `mount` output, including fuse-t: lines
  volumes.py       Volume model, build_volumes, resolve by label/uuid/device/mount
  options.py       ntfs-3g argument assembly and stderr classification
  doctor.py        environment checks with actionable fixes
  mounter.py       mount orchestration, competitor eviction, write probe
  unmounter.py     safe unmount, SIGTERM fallback, eject
  privileged.py    single privilege-escalation path: sudo re-exec via need_root
  models.py        Device dataclass

tests/
  test_*.py        unit tests with injected runners
  fixtures/        captured diskutil / mount output
  manual/
    acceptance.md  human acceptance procedure (HD1 only)
    fuset-mount-test.sh  older root-only reference probe (verbatim)
```

## Module responsibilities

- `diskutil.py` / `mounttable.py` — parse live system state.
- `volumes.py` — combine devices and mounts; determine `rw`/`ro`/`unmounted`.
- `options.py` — build ntfs-3g options, classify failures (hibernated, dirty,
  busy, EPERM).
- `doctor.py` — check the host; hard failures are `fuse-t`, `ntfs-3g`, `root`,
  `device-access`.
- `mounter.py` — unmount Apple's mount, run ntfs-3g, evict Apple remounts,
  write-probe, restore read-only on any failure.
- `unmounter.py` — unmount FUSE-T and Apple mounts, optionally restore
  read-only, then eject parent disk.
- `privileged.py` — the only root-escalation path; `os.execvp` through `sudo`.
- `cli.py` — ties it together; resolves volume *before* privilege escalation,
  confirms destructive flags *before* escalation.

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
   mount, so every exit path must restore it. `_fail` and the `except Exception`
   tail call `_restore_read_only`. A failed mount must not leave the volume
   unmounted or half-mounted.
6. **No guesses on duplicate labels.** `resolve()` raises `AmbiguousVolume` and
   the CLI lists candidates; never pick the first match.
7. **No write without a probe.** `mounter.py` writes, reads back, and deletes
   `.ntfsmnt-probe` before reporting success.
8. **Hibernated and dirty volumes are refused by default.** `--recover` and
   `--remove-hiberfile` are opt-in only.
9. **All external effects go through injected callables.** Production passes
   `real_run`, `read_mount_table`, `time.sleep`, and the real probe; tests pass
   fakes. Every subprocess effect (`diskutil`, `ntfs-3g`, `mount`, `pgrep`,
   `kill`) goes through an injected callable (`run` or `read_mount_table`).
   `_ensure_mount_point` and `_write_probe` are injected or monkeypatched in
   tests, so no test writes to `/Volumes`.

When changing `mounter.py` or `unmounter.py`, first verify the failure-path
sequence still restores the read-only mount and does not add a `SIGKILL`.

## Conventions

- Keep the README, this file, and `tests/manual/acceptance.md` honest about
  what is verified and what is not. Do not invent commands or promise behaviour
  the code does not implement.
- Do not hand-edit `/Users/akshayharti/workspace/REGISTRY.json`; the controller
  handles it.
