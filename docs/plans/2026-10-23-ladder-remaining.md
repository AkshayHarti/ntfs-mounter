# ntfsmnt — remaining ladder: Homebrew, companion app, site — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the rest of the distribution ladder on top of the PyPI release: the CI action bumps, a Homebrew tap-in-repo (formula for the FUSE-T-linked `ntfs-3g` plus `ntfsmnt`), an unsigned menu-bar companion app with signing scripts for later notarization, and a static GitHub Pages site.

**Spec decisions:** Tier 3 ships unsigned + signing scripts (no Apple Developer ID yet). The tap is `Formula/` inside this repo, installed by URL (`brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter`).

**Branch:** `feat/ladder` (already created from `origin/main`).

## Hard technical facts (verified on this machine)

- `ntfs-3g` path is hardcoded at `/usr/local/bin/ntfs-3g` in `options.py:1` and `doctor.py:6`. A Homebrew install lands in `/opt/homebrew/bin`, invisible to root's `sudo` `secure_path`.
- `privileged.py` re-execs the CLI via `sudo` with `env_reset`; PATH resets. Resolution therefore must not depend on PATH inside root: add fixed fallback candidates (`/opt/homebrew/bin`, `/usr/local/bin`) after `shutil.which`, plus a `NTFSMNT_NTFS3G` override that wins over everything.
- The pushed `/usr/local/bin/ntfs-3g` binary has `LC_RPATH` `/usr/local/lib`, because FUSE-T's dylib install name is `@rpath/libfuse-t.dylib`. The formula must add `-Wl,-rpath,/usr/local/lib` to `LDFLAGS` or the built binary will not load `libfuse-t`.
- The fork's `src/Makefile.am` hardcodes `-I/usr/local/include/fuse` and `-lfuse-t`; `./configure` needs no extra flags.
- `web/service.py` mounts run in-process; the server is expected to run as root (`sudo ntfsmnt web`), so the app must hand off through Terminal, not embed a server.
- Latest action majors (verified 2026-10-23): checkout v7, setup-python v7, setup-uv v10, upload-artifact v7, download-artifact v8, configure-pages v6, upload-pages-artifact v5, deploy-pages v5.

## Workstreams and file ownership

| File(s) | Owner |
|---|---|
| `.github/workflows/test.yml`, `.github/workflows/release.yml` | Integrator (W1) |
| `ntfsmnt/paths.py`, `ntfsmnt/options.py`, `ntfsmnt/doctor.py`, `tests/test_paths.py` | WS-A |
| `Formula/ntfs-3g-fuse-t.rb`, `Formula/ntfsmnt.rb`, `README.md`, `docs/HOMEBREW.md` | WS-B |
| `app/MenuBar.swift`, `app/Info.plist`, `scripts/build-app.sh`, `scripts/sign-app.sh`, `docs/APP.md` | WS-C |
| `site/**`, `.github/workflows/pages.yml`, `docs/SITE.md` | WS-D |
| `AGENTS.md` | Integrator |

Do not edit another workstream's files. Never commit, push, or switch branches.

## W1 — CI action bumps + runner pin (integrator)

- [ ] `test.yml` + `release.yml`: reader pin `runs-on: ubuntu-24.04` (the `ubuntu-latest` migration to Ubuntu 26 is underway as of 2026-10).
- [ ] Bump actions to the Node 24 majors above.
- [ ] Confirm both files parse (PyYAML), the original job structure is unchanged, and `pypa/gh-action-pypi-publish` stays on the `release/v1` ref. Do not bump the pypi-publish action if no compatible major exists.

## WS-A — ntfs-3g path resolution

**Files:** `ntfsmnt/paths.py` (new), `ntfsmnt/options.py`, `ntfsmnt/doctor.py`, `tests/test_paths.py` (new)

- [ ] **A1** `ntfsmnt/paths.py`:
  ```python
  def resolve_ntfs3g() -> str:
      override = os.environ.get("NTFSMNT_NTFS3G")      # wins always
      if override and os.path.isfile(override): return override
      found = shutil.which("ntfs-3g")                  # user's PATH
      if found: return found
      for candidate in ("/opt/homebrew/bin/ntfs-3g",
                        "/usr/local/bin/ntfs-3g"):     # sudo secure_path safe
          if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
              return candidate
      return "/usr/local/bin/ntfs-3g"                  # previous default
  ```
  Plus `resolve_probe(ntfs3g)` returning the `ntfs-3g.probe` beside the binary if
  the binary is absolute, else `"ntfs-3g.probe"`.
  Module constants `NTFS3G = resolve_ntfs3g()`, `NTFS3G_PROBE = resolve_probe(NTFS3G)`.
  **Adjusted during implementation:** candidates outrank PATH
  (`override → candidates → which → fallback`) because Homebrew's other ntfs-3g
  formulas share `/opt/homebrew/bin/ntfs-3g` with a macFUSE linkage; if such a
  binary wins, the doctor's FUSE-T linkage check must report it honestly.
- [ ] **A2** `options.py`: `NTFS3G_BIN = paths.NTFS3G` (import time), `doctor.py`: `NTFS_3G`/`NTFS_3G_PROBE` likewise. Existing `monkeypatch` tests keep working because `build_mount_args` and `doctor` read the module attributes at call time.
- [ ] **A3** `tests/test_paths.py`: override wins; PATH hit wins; empty PATH falls back to `/opt/homebrew/bin/ntfs-3g` when that file exists; the candidate list is a module-level tuple so tests can monkeypatch it. Probe derivation: absolute parent + `ntfs-3g.probe`.
- [ ] **A4** `tests/test_doctor.py` or similar files that patch `doctor.NTFS_3G` must pass untouched; if any test imports `options.NTFS3G_BIN` as a value rather than the attribute, fix that test file as the exception.
- [ ] **A5** Verify: `uv run pytest -q` (284 baseline, +); and `uv run python -c "from ntfsmnt import options, doctor; print(options.NTFS3G_BIN, doctor.NTFS_3G)"` prints `/usr/local/bin/ntfs-3g` on this Mac.

## WS-B — Homebrew formulas

**Files:** `Formula/ntfs-3g-fuse-t.rb`, `Formula/ntfsmnt.rb`, `README.md`, `docs/HOMEBREW.md`

- [ ] **B1** `Formula/ntfs-3g-fuse-t.rb`:
  - `desc "Read/write NTFS-3G linked against FUSE-T"`, `homepage`, `url` = the GitHub tarball of a pinned commit SHA of `macos-fuse-t/ntfs-3g` (`https://github.com/macos-fuse-t/ntfs-3g/archive/<sha>.tar.gz`), compute the SHA256 with `curl | shasum -a 256` and note it in the comment.
  - `depends_on cask: "fuse-t"`, `depends_on "autoconf" => :build`, `depends_on "automake" => :build`, `depends_on "libtool" => :build`, `depends_on "pkgconf" => :build`.
  - `ENV["LDFLAGS"] = "-Wl,-rpath,/usr/local/lib"` before configure.
  - `def install`: `system "./autogen.sh"; system "./configure", "--prefix=#{prefix}", "--disable-ntfsprogs" is NOT allowed (the probe lives in ntfsprogs tools? verify: `ntfs-3g.probe` is in src/, not ntfsprogs — if so `--disable-ntfsprogs` is fine and faster); `system "make", "install"`.
  - `test do: system bin/"ntfs-3g", "--version"`.
- [ ] **B2** `Formula/ntfsmnt.rb`: virtualenv formula from the PyPI sdist `ntfsmnt-0.1.0`, `depends_on "python@3.13"`, `depends_on "ntfs-3g-fuse-t"`, web extra as `resource`s (include the transitive closure: annotated-types, anyio, click, h11, idna, pydantic, pydantic-core, sniffio, starlette, typing-extensions, uvicorn-standard extras if used — generate with `brew update-python-resources --print-only` or by hand from the lockfile). Serve both CLI and web.
  - `conflicts_with` none; postinstall does NOT touch /usr/local.
  - `test do: system bin/"ntfsmnt", "--help"`.
- [ ] **B3** README: add a "Homebrew" install block under the PyPI block:
  ```bash
  brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter
  brew install AkshayHarti/ntfs-mounter/ntfsmnt
  ```
  and add the formulas to the Uninstall section. `docs/HOMEBREW.md` explains the two formulas and how they differ from the setup.sh build, plus tap testing commands.
- [ ] **B4** Verify: `brew style --fix Formula/` (or `brew audit --strict --formula ./Formula/*.rb`), and run the real local-tap test *after the path fix has landed in the venv* (check `grep -n NTFSMNT_NTFS3G ntfsmnt/paths.py` first; if missing, stop before install):
  ```
  rm -rf /tmp/tap-test && git clone -q "$PWD" /tmp/tap-test
  brew tap AkshayHarti/ntfs-mounter /tmp/tap-test
  brew install AkshayHarti/ntfs-mounter/ntfsmnt
  /opt/homebrew/bin/ntfsmnt doctor    # must resolve /opt/homebrew/bin/ntfs-3g
  ```
- [ ] **B5** Report: formula names, resolved deps, `brew audit` findings, and whether the tap install ran.

## WS-C — Menu-bar companion app (unsigned v0)

**Files:** `app/MenuBar.swift`, `app/Info.plist`, `scripts/build-app.sh`, `scripts/sign-app.sh`, `docs/APP.md`

- [ ] **C1** `app/MenuBar.swift`: AppKit menu-bar app (`LSUIElement` = 1):
  - Locate `ntfsmnt` via PATH + `/opt/homebrew/bin` + `/usr/local/bin` + `~/.local/bin`.
  - Menu: **Open dashboard**, **Run doctor**, **Setup guide** (opens the Pages site), separator, **Quit**.
  - Both action items write `~/Library/Application Support/ntfsmnt/<name>.command` (`sudo <ntfsmnt> web --open` / `<ntfsmnt> doctor; …`), `chmod 700`, and open it with `NSWorkspace` — Terminal prompts for the password; **no `osascript`, no embedded server, no AppleScript bumps**.
  - If `ntfsmnt` is missing, show an NSAlert with the three install commands and offer to open the site.
- [ ] **C2** `app/Info.plist`: bundle id `dev.akshayharti.ntfsmnt`, `LSUIElement` true, short version 0.1.0, no entitlements.
- [ ] **C3** `scripts/build-app.sh`: compiles with `swiftc -O -framework Cocoa -o`, assembles `dist/ntfsmnt.app` (Contents/MacOS, Info.plist), ad-hoc signs `codesign --force --deep --sign -`, prints `codesign -dv` and `spctl`/`plutil -lint` checks.
- [ ] **C4** `scripts/sign-app.sh`: `--developer-id <TEAM_ID|-adhoc>`; with a team id: `codesign --force --options runtime --sign "Developer ID Application: …"` then `xcrun notarytool submit` (keychain profile name read from `NOTARY_KEYCHAIN_PROFILE`, default recommended) and `xcrun stapler staple`. Without a team id, exit 1 with usage. Never produce output files in the repo root (use `dist/`).
- [ ] **C5** `docs/APP.md`: what the app is and is not (a launcher, not a privileged helper; FDA stays on Terminal), unsigned install instructions (`xattr -d com.apple.quarantine` or Control-click Open), and the full notarization walk-through for later.
- [ ] **C6** Verify: `bash scripts/build-app.sh`, `plutil -lint dist/ntfsmnt.app/Contents/Info.plist`, `codesign --verify --deep --strict dist/ntfsmnt.app`, no `osascript` in sources.
- [ ] **C7** Report: app paths, builder output, whether the app launches (manual step for the user if not verified).

## WS-D — GitHub Pages site

**Files:** `site/index.html`, `site/styles.css`, `site/favicon.svg`, `.github/workflows/pages.yml`, `docs/SITE.md`

- [ ] **D1** `site/index.html` — single `index.html` + one stylesheet, no JS dependencies, no build step, no preprocessor framework; hand-built, dark-friendly, mobile responsive. Sections in this order:
  1. Hero: what it does in one sentence + the three install commands (prerequisites / PyPI / Homebrew).
  2. **The problem**: macOS mounts NTFS read-only (FSKit); paid alternatives; data-loss risk of third-party hacks.
  3. **The solution**: CLI + local web UI on top of FUSE-T + ntfs-3g.
  4. **Design**: the safety invariants (sudo-only escalation, restore Apple's read-only mount on any failure, no auto-repair, no `osascript`, `kill -9`-free loopback-only UI with token auth, single operation lock).
  5. **How to run**: prerequisites, one-command setup, three sections of install (prereqs script, `uv tool install`, brew), doctor.
  6. **Audience**: who each feature is for (owner, technical users, community; non-technical still not served).
  7. **Contribute**: dev setup, test command, invariants to respect, link to contributing docs/issues.
  8. Footer: MIT, PyPI link, GitHub link, version 0.1.0.
- [ ] **D2** `.github/workflows/pages.yml`: `on: push: branches: [main] paths: ["site/**"]` + `workflow_dispatch`; `permissions: pages: write, id-token: write, contents: read`; concurrency `pages` + `cancel-in-progress: true`; jobs: build (checkout@v7, configure-pages@v6, upload-pages-artifact@v5 with `path: site`) → deploy (`environment:` `github-pages`, `deploy-pages@v5`).
- [ ] **D3** `docs/SITE.md`: what the site is, how to preview locally (`python3 -m http.server -d site`), how to enable Pages (one-time `gh api -X POST .../pages -f build_type=workflow`, already done pre-merge), URL `https://akshayharti.github.io/ntfs-mounter/` (lowercase repo name in the slug).
- [ ] **D4** Verify: every internal link/anchor resolves; `python3 -m http.server` smoke test; no external requests at page load (grep for `http` in HTML beyond canonical/footer links); YAML parses.

## WS-E — Review and verification (after A–D)

- [ ] **E1** Reviewer (read-only): spec/plan compliance, safety in intact, no osascript, formulas correct (URL/sha256/rpath/cask dep), PATH-resolution ordering, pages workflow permissions minimal (`pages: write, id-token: write` only on deploy, `contents: read` top), app build script does not modify outside `dist/`.
- [ ] **E2** Tester: full pytest; `uv build` + clean-venv smoke; `bash scripts/setup.sh --check`; `bash scripts/build-app.sh` + verify structure; local brew tap install if WS-B completed it; `python3 -m http.server -d site` fetch the homepage and check HTTP 200 + content contains "The problem".
- [ ] **E3** Integrator: fix findings, update `AGENTS.md` (new files, paths module, formulas, app, site), commit in reviewable chunks, push `feat/ladder`, open the PR.

## Verification commands

```bash
cd ~/workspace/repos/ntfs-mounter
uv run pytest -q
uv build
bash scripts/setup.sh --check
bash scripts/build-app.sh
brew audit --strict --formula ./Formula/*.rb
python3 -m http.server -d site   # then curl -s localhost:8000 | grep -c "The problem"
```
