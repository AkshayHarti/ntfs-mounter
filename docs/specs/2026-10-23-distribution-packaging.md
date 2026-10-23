# ntfsmnt distribution and packaging — design spec

**Date:** 2026-10-23
**Status:** approved for implementation (Tier 0 + Tier 1)
**Plan:** `docs/plans/2026-10-23-distribution-packaging.md`

## Problem

`ntfsmnt` works, but sharing it means walking someone through four manual
steps: install the FUSE-T cask, build the `macos-fuse-t/ntfs-3g` fork from
source against `libfuse-t`, clone this repo, and `uv sync`. Nothing is
discoverable, nothing is reproducible, and every new Mac repeats the same
undocumented build.

The Python part is already clean (stdlib-only runtime, one console script,
`web` as an extra), so packaging the code is not the hard part. The hard part
is the environment around it: FUSE-T, the FUSE-T-linked `ntfs-3g`, root, and
the Full Disk Access grant.

## Audience

| Audience | What they need |
|---|---|
| Owner's other Macs | One documented command sequence, no memory required |
| Technical Mac users | `uv tool install ntfsmnt` / `pipx install ntfsmnt` + a setup script |
| Open-source community | PyPI release, licence, contribution/release docs, green CI |
| Non-technical users | **Not served by this spec** — see non-goals |

## Goals

- **G1** `uv tool install ntfsmnt` and `pipx install ntfsmnt` work from PyPI,
  with `ntfsmnt[web]` for the UI.
- **G2** `scripts/setup.sh` takes a clean Apple Silicon Mac from zero to a
  passing `ntfsmnt doctor` for the non-Python prerequisites. Idempotent, no
  root execution, `--check` is strictly read-only.
- **G3** README quickstart: three commands to a first read-write mount, honest
  about root and Full Disk Access.
- **G4** Releases are reproducible and tokenless: pushing tag `vX.Y.Z` builds
  sdist + wheel and publishes to PyPI through OIDC trusted publishing. One
  source of truth for the version.
- **G5** Safety invariants unchanged: no `osascript`, no automatic repair
  tools, no `kill -9` in tool code, failed mounts restore Apple's read-only
  mount. Runtime stays stdlib-only; `web` stays an optional extra.

## Non-goals

- Homebrew formula/tap (Tier 2) and the `ntfs-3g` formula build.
- Notarized menu-bar `.app` (Tier 3). Non-technical users are not served yet;
  a companion app is a separate project with its own privilege design.
- Automating the Full Disk Access grant (macOS requires the user's click).
- Linux, Intel Macs, or macOS < 26 certification.

## Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | PyPI name `ntfsmnt` | Free as of 2026-10-23 (`/pypi/ntfsmnt/json` → 404) |
| D2 | Version `0.1.0`, tag `v0.1.0`, single source in `ntfsmnt/__init__.py` via hatchling dynamic version | Removes the duplicate `pyproject.toml` + `__init__.py` version |
| D3 | MIT licence | Most common permissive default; change before first publish if the owner prefers another |
| D4 | Trusted publishing: workflow `release.yml`, environment `pypi`, repo `AkshayHarti/ntfs-mounter`, `id-token: write` | No API token or secret to leak; same OIDC pattern as npm |
| D5 | `setup.sh` builds `macos-fuse-t/ntfs-3g` from `edge` (no releases exist), override with `NTFS3G_REF`, prints the resolved commit | Upstream has no tags; printing the SHA keeps a build traceable |
| D6 | `setup.sh` never runs as root; it calls `sudo` only for `make install` and says so | Matches the repo's privilege model |
| D7 | CI keeps `ubuntu-latest` for unit tests and adds a `build` job | Tests are hardware-free; no macOS runner cost |
| D8 | No new runtime dependencies; packaging test uses stdlib `tomllib` | Preserves G5 |

## Interfaces

### `scripts/setup.sh`

```
scripts/setup.sh [--check] [--yes]
```

- `--check` — read-only. Inspects macOS, uv, FUSE-T, `ntfs-3g` and its
  `libfuse-t` link, prints `ok:` / `MISSING:` / `WARN:` lines, exits 0.
  Never calls brew, sudo, git, or make.
- default — installs what is missing: FUSE-T cask (if absent), autotools +
  pkgconf via brew (if absent), clones/updates `macos-fuse-t/ntfs-3g` into
  `${XDG_CACHE_HOME:-$HOME/Library/Caches}/ntfsmnt/ntfs-3g`, runs
  `./autogen.sh && ./configure && make`, then `sudo make install`.
- `--yes` — do not prompt before the install steps (still prompts for the
  sudo password).
- Exit codes: `0` success or already satisfied, `1` a step failed, `2`
  unsupported platform (not macOS) — except in `--check` mode, which always
  exits 0 so it is usable from CI.

### Release workflow

- Trigger: `push` of tags matching `v*`.
- Jobs: `build` (sdist + wheel, `uv build`) → `publish`
  (`pypa/gh-action-pypi-publish@release/v1`, `environment: pypi`,
  `permissions: id-token: write`).
- Fails if the tag does not equal `v` + the version from the built metadata.

### `pyproject.toml`

- `dynamic = ["version"]`, `[tool.hatch.version] path = "ntfsmnt/__init__.py"`.
- `license = "MIT"`, `license-files = ["LICENSE"]`, classifiers, project URLs.
- Unchanged: `requires-python = ">=3.12"`, `[project.scripts]`, `web` extra.

## Acceptance

- **A1** `uv build` produces `ntfsmnt-0.1.0.tar.gz` and a wheel; the wheel
  installs into a clean venv and `ntfsmnt --help` exits 0.
- **A2** `bash scripts/setup.sh --check` exits 0 on this Mac and reports every
  prerequisite as `ok:`; on a machine with a missing prerequisite it reports
  `MISSING:` with the exact install command and still exits 0.
- **A3** `uv run pytest -q` green, including new packaging and setup-script
  tests; documented test count updated.
- **A4** README quickstart matches what the script actually does; no step is
  described that the script does not perform.
- **A5** After the owner registers the PyPI pending publisher, pushing
  `v0.1.0` publishes without any repository secret. Verified once, manually.

## Risks

| Risk | Mitigation |
|---|---|
| Upstream `edge` moves under us | `NTFS3G_REF` override; script prints the resolved commit |
| Name squatting before first publish | Publish 0.1.0 promptly after merge |
| First release needs a manual PyPI pending publisher | Documented in `docs/RELEASING.md`; workflow cannot self-register |
| FUSE-T cask install needs an admin GUI prompt | Script says so before running; `--check` lets users inspect first |
| FDA cannot be automated | README states it as a required manual step, with the exact System Settings path |
