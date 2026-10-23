# ntfsmnt distribution packaging — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish `ntfsmnt` to PyPI with tokenless releases, and make a clean Mac installable with one setup script plus an honest README quickstart.

**Architecture:** Three disjoint workstreams (packaging metadata, release/CI, quickstart) that touch different files and run in parallel, followed by one review + one verification pass. No runtime code changes, no new runtime dependencies.

**Tech Stack:** Python ≥3.12, hatchling, uv, pytest, GitHub Actions, PyPI trusted publishing (OIDC), zsh/bash setup script.

**Spec:** `docs/specs/2026-10-23-distribution-packaging.md`

## Global Constraints

- No change to `ntfsmnt/` runtime code, safety invariants, or the `web` extra's contents.
- No new runtime dependencies. `tomllib` (stdlib) for the packaging test.
- Every workstream leaves `uv run pytest -q` green and does not commit or push.
- Branch: `feat/distribution` (already created from `origin/main`).
- Do not touch files owned by another workstream. `AGENTS.md` is owned by the integrator.

## File Structure

| File | Owner | Responsibility |
|---|---|---|
| `pyproject.toml` | WS-A | Package metadata, dynamic version, licence, URLs |
| `LICENSE` | WS-A | MIT licence text |
| `tests/test_packaging.py` | WS-A | Metadata + version single-source regression |
| `.github/workflows/release.yml` | WS-B | Tag → build → PyPI trusted publish |
| `.github/workflows/test.yml` | WS-B | Add wheel build + smoke-install job |
| `docs/RELEASING.md` | WS-B | Pending publisher setup, tag procedure, rollback |
| `scripts/setup.sh` | WS-C | Idempotent prerequisite installer + `--check` |
| `tests/test_setup_script.py` | WS-C | `--check` runs everywhere, read-only, correct output |
| `README.md` | WS-C | Requirements / Install / Uninstall quickstart |
| `AGENTS.md` | Integrator | Test count, new files, release pointer |

## Workstream A — packaging metadata

**Files:** `pyproject.toml`, `LICENSE`, `tests/test_packaging.py`

- [ ] **A1** Make the version single-source. In `pyproject.toml`:
  - remove `version = "0.1.0"`, add `dynamic = ["version"]` to `[project]`;
  - add `[tool.hatch.version] path = "ntfsmnt/__init__.py"`;
  - add `license = "MIT"`, `license-files = ["LICENSE"]`, `readme = "README.md"`,
    `description`, `keywords`, `classifiers` (Environment :: MacOS X,
    Operating System :: MacOS, Programming Language :: Python :: 3.12/3.13,
    Topic :: System :: Filesystems), and `[project.urls]` (Homepage, Issues,
    Repository).
  - Do not change `requires-python`, `[project.scripts]`, the `web` extra,
    `[dependency-groups]`, or the hatch wheel target.
- [ ] **A2** Add `LICENSE` with the MIT text, copyright `2026 AkshayHarti`.
- [ ] **A3** Add `tests/test_packaging.py` (stdlib only, no `pytest.importorskip`):
  - `test_name_and_entry_point` — `name == "ntfsmnt"`,
    `project.scripts.ntfsmnt == "ntfsmnt.cli:main"`.
  - `test_version_is_single_source` — `version` is not in `[project]`,
    `dynamic == ["version"]`, `tool.hatch.version.path == "ntfsmnt/__init__.py"`,
    and the `__version__` literal parsed from that file equals
    `ntfsmnt.__version__`.
  - `test_web_extra_unchanged` — `web` extra still lists fastapi, uvicorn,
    jinja2.
  - `test_licence_declared` — `license == "MIT"`, `LICENSE` exists and starts
    with `MIT License`.
  - `test_runtime_is_stdlib_only` — `[project]` has no `dependencies` or an
    empty list.
- [ ] **A4** Verify: `uv build` writes both artifacts; `uv run pytest -q
  tests/test_packaging.py` passes; `uv run pytest -q` still 274 + 5 passed.
- [ ] **A5** Report the exact `dist/` filenames and the new test count.

## Workstream B — release workflow and CI

**Files:** `.github/workflows/release.yml`, `.github/workflows/test.yml`, `docs/RELEASING.md`

- [ ] **B1** Add `.github/workflows/release.yml`:
  - `on: push: tags: ["v*"]`, `permissions: contents: read`.
  - Job `build` (ubuntu-latest): checkout, `astral-sh/setup-uv@v5`, `uv build`,
    assert `v${version}` equals `$GITHUB_REF_NAME` using
    `python -c "import ntfsmnt; print(ntfsmnt.__version__)"`, upload `dist/`
    as an artifact.
  - Job `publish` (`needs: build`, `environment: pypi`,
    `permissions: id-token: write`): download artifact,
    `pypa/gh-action-pypi-publish@release/v1` with `packages-dir: dist/`.
  - No secrets, no API tokens.
- [ ] **B2** Extend `.github/workflows/test.yml` with a `build` job:
  `uv build`, then a clean-venv wheel smoke test
  (`python -m venv`, `pip install dist/*.whl`, `ntfsmnt --help`). Keep the
  existing `test` job unchanged.
- [ ] **B3** Add `docs/RELEASING.md` covering, in order: the one-time PyPI
  pending-publisher registration (project `ntfsmnt`, owner `AkshayHarti`,
  repo `ntfs-mounter`, workflow `release.yml`, environment `pypi`); the
  version bump (`ntfsmnt/__init__.py` only); `uv run pytest -q`; commit, tag
  `vX.Y.Z`, push the tag; what success looks like (Actions run, PyPI page,
  `uv tool install ntfsmnt`); and rollback (yank on PyPI, never re-tag; bump
  patch instead).
- [ ] **B4** Verify: `actionlint` if present, otherwise
  `python -c "import yaml,sys; [yaml.safe_load(open(p)) for p in ...]"` with
  `pyyaml` from a throwaway `uv run --with pyyaml`, and `bash -n` on any
  shell snippets. Do not run the publish job.
- [ ] **B5** Report the workflow names, job names, and the exact tag pattern.

## Workstream C — setup script and README

**Files:** `scripts/setup.sh`, `tests/test_setup_script.py`, `README.md`

- [ ] **C1** Add `scripts/setup.sh` (bash, `#!/usr/bin/env bash`,
  `set -euo pipefail`), executable. Behaviour:
  - `--check`: read-only. Print `ok:`/`MISSING:`/`WARN:` for macOS version,
    `uv`, `/usr/local/lib/libfuse-t.dylib`, `/usr/local/bin/ntfs-3g`, and
    `otool -L … | grep libfuse-t`. Exit 0 always. No brew/sudo/git/make.
  - default: fail with exit 2 if not macOS. Skip satisfied steps. Install
    FUSE-T with `brew install --cask fuse-t` when the dylib is missing.
    Ensure `autoconf automake libtool pkgconf` via brew when `autogen.sh`
    would fail. Clone `https://github.com/macos-fuse-t/ntfs-3g.git` at
    `${NTFS3G_REF:-edge}` into
    `${XDG_CACHE_HOME:-$HOME/Library/Caches}/ntfsmnt/ntfs-3g` (fetch+checkout
    when it exists), print the resolved `git rev-parse HEAD`, then
    `./autogen.sh && ./configure && make`, then
    `sudo make install`.
  - `--yes` skips the confirmation prompt before the mutating steps.
  - Print the Full Disk Access reminder at the end (System Settings → Privacy
    & Security → Full Disk Access → enable the terminal) and suggest
    `uv tool install "ntfsmnt[web]"` + `ntfsmnt doctor`.
  - Never use `osascript`, never run the whole script under sudo.
- [ ] **C2** Add `tests/test_setup_script.py` (stdlib only):
  - `test_check_mode_exits_zero` — run `bash scripts/setup.sh --check` with
    `subprocess.run`, assert rc 0.
  - `test_check_mode_is_read_only_and_informative` — assert stdout contains
    `FUSE-T`, `ntfs-3g`, and at least one of `ok:`/`MISSING:`/`WARN:`.
  - `test_script_is_executable` — `os.access(..., os.X_OK)`.
  - `test_no_osascript` — script text does not contain `osascript`.
  - Skip nothing: these must pass on Linux CI too.
- [ ] **C3** Rewrite `README.md` sections `## Requirements`, `## Install`,
  `## Uninstall`:
  - Requirements table keeps the verified versions but says "run
    `scripts/setup.sh` to install FUSE-T and the FUSE-T-linked ntfs-3g".
  - Install becomes three blocks: (1) prerequisites
    `git clone … && cd ntfs-mounter && ./scripts/setup.sh`, (2) PyPI
    `uv tool install "ntfsmnt[web]"` (with `pipx install "ntfsmnt[web]"` as
    the alternative), (3) from checkout for contributors (`uv sync --extra
    web`, `uv run ntfsmnt …`).
  - Keep the Full Disk Access and sudo sections; link them from the quickstart.
  - Uninstall adds `pipx uninstall ntfsmnt` and notes the cache build dir.
  - Do not claim any step the script does not perform.
- [ ] **C4** Verify: `bash -n scripts/setup.sh`,
  `bash scripts/setup.sh --check` (report output), and
  `uv run pytest -q tests/test_setup_script.py`. Full suite green.
- [ ] **C5** Report the `--check` output verbatim.

## Workstream D — review and verification (after A, B, C)

- [ ] **D1** Reviewer (read-only): diff against `origin/main`; check spec
  compliance, file ownership, safety invariants, no new runtime deps, no
  secrets, no `osascript`, no root execution in `setup.sh`, README claims
  match the script, workflow cannot publish without a tag.
- [ ] **D2** Tester: `uv run pytest -q`; `uv build`; clean-venv wheel install
  + `ntfsmnt --help`; `bash scripts/setup.sh --check`; `git diff --check`;
  confirm `ntfsmnt doctor` still behaves (non-root output is fine).
- [ ] **D3** Integrator: fix findings, update `AGENTS.md` (test count, new
  files, `docs/RELEASING.md` pointer), commit in reviewable chunks, push
  `feat/distribution`, open the PR with the spec and evidence.

## Verification Commands

```bash
cd ~/workspace/repos/ntfs-mounter
uv run pytest -q
uv build
ls dist/
python -m venv /tmp/ntfsmnt-smoke && /tmp/ntfsmnt-smoke/bin/pip install dist/*.whl
/tmp/ntfsmnt-smoke/bin/ntfsmnt --help
bash scripts/setup.sh --check
git diff --check
```
