# Seamless app + npm channel — implementation plan

**Spec:** `docs/specs/2026-10-24-seamless-app-and-npm.md` (contract there is frozen)
**Branch:** `feat/seamless-app-npm`

## File ownership

| Workstream | Files |
|---|---|
| WS-1 privileged install | `scripts/install-privileged.sh`, `tests/test_install_privileged.py` |
| WS-2 app | `app/MenuBar.swift`, `app/Info.plist`, `scripts/build-app.sh`, `scripts/install-app.sh`, `docs/APP.md` |
| WS-3 npm | `npm/package.json`, `npm/bin/ntfsmnt.js`, `npm/README.md`, `npm/test/proxy.test.js`, `.github/workflows/npm.yml`, `docs/NPM.md`, `tests/test_npm_package.py` |
| WS-4 integrator | `.github/workflows/test.yml`, `AGENTS.md`, `README.md`, `site/index.html`, spec/plan |

Do not edit another workstream's files. Never commit, push, or switch branches.

## WS-1 — privileged installer

- [ ] `install-privileged.sh` implementing the contract exactly: root check, Python ≥3.12 via `uv` or `python3.12+`, venv at `/usr/local/libexec/ntfsmnt`, `ntfsmnt[web]==<version>` (version from `ntfsmnt/__init__.py`, `--version` override, `--sdist` for local wheels), `chown -R root:wheel`, `chmod -R go-w`, `visudo -cf` on a temp file, then `install -m 0440 -o root -g wheel` to `/etc/sudoers.d/ntfsmnt`.
- [ ] `--check` read-only and exit 0 anywhere: reports root, python, venv, ownership, mode, rule text, `sudo -n -l`.
- [ ] `--uninstall`: remove the sudoers file first, then the venv.
- [ ] Refuse when the venv path is group/other-writable, when not root, and when `visudo` is missing.
- [ ] `tests/test_install_privileged.py` (mirror `test_setup_script.py`): `bash -n`; rule text restricted to `web *`; `visudo -cf` appears before install; `0440`/`root:wheel`; uninstall removes rule before venv; no `osascript`, no `kill -9`; `--check` exits 0 unprivileged.
- [ ] Verify: `bash -n`, `bash scripts/install-privileged.sh --check` (unprivileged), `uv run pytest -q tests/test_install_privileged.py`.

## WS-2 — app

- [ ] `MenuBar.swift`: detection via `sudo -n -l <path> web --port 0 --owner <uid>:<gid>` (full argv — the rule has args); states Setup / Stopped / Starting / Running / Stopping; **Set up…** opens the bundled installer `.command`; **Start dashboard** spawns the contract command, reads stdout for `^ntfsmnt web: (http://\S+)$`, opens the URL; **Open dashboard** re-opens the current URL; **Stop dashboard** and **Quit** send `SIGTERM`, wait 5 s, then force only this app's server tree; **Grant Full Disk Access…** opens the FDA pane; **Setup guide**.
- [ ] No `osascript`. Shell quoting safe for paths with spaces. No `Process` use except the contract command and doctor.
- [ ] `build-app.sh` copies `scripts/install-privileged.sh` into `Contents/Resources` and generates `install-privileged.command`; keeps ad-hoc signing and the existing verifications.
- [ ] `install-app.sh`: build, copy to `/Applications` (default) or `~/Applications` (`--user`), `xattr -dr com.apple.quarantine`, `--launch`.
- [ ] `docs/APP.md`: rewrite for the seamless flow — setup, FDA, start/stop, uninstall (rule first), the standing-grant tradeoff, and what is *not* verified.
- [ ] Verify: `bash scripts/build-app.sh`, `plutil -lint`, `codesign --verify --deep --strict`, installer present in `Contents/Resources`, `grep -r osascript app/` empty, `bash scripts/install-app.sh --user --dry-run` (or build-only path).

## WS-3 — npm

- [ ] `npm/bin/ntfsmnt.js`: resolution order from the spec, realpath recursion guard, `uvx` fallback, stdio inherit, signal forwarding, exit code `128 + signal` on signal, install help on failure, `process.platform !== "darwin"` message.
- [ ] `npm/package.json`: name `ntfsmnt`, version equal to the Python version, `bin`, `files: ["bin"]`, `engines.node >= 18`, no dependencies, `repository`, `license MIT`, `os: ["darwin"]` omitted (wrapper explains instead).
- [ ] `npm/test/proxy.test.js` with `node:test`: fake CLI in a temp dir via `NTFSMNT_BIN`; argv passthrough; exit code passthrough; missing binary → help text + exit 1; recursion guard (PATH containing the wrapper) does not exec itself; signal exit code.
- [ ] `.github/workflows/npm.yml`: `v*` tags + `workflow_dispatch`; `actions/setup-node@v7` with `registry-url: https://registry.npmjs.org`; `permissions: id-token: write, contents: read`; `npm publish --provenance --access public` in `npm/`; `npm test` before publish.
- [ ] `docs/NPM.md`: package behaviour, `npx` usage, the one-time npm trusted-publisher setup (repo + workflow + environment), version-sync rule, rollback (`npm unpublish` window).
- [ ] `tests/test_npm_package.py`: `package.json` version == `ntfsmnt.__version__`; no `dependencies`; `bin.ntfsmnt` points at an existing file with a `#!/usr/bin/env node` shebang; `files` covers every shipped path; no `osascript` in the wrapper.
- [ ] Verify: `cd npm && npm test`, `npm pack --dry-run`, `uv run pytest -q tests/test_npm_package.py`.

## WS-4 — integrator

- [ ] `test.yml`: add a `node --test npm/test` step (node is on `ubuntu-24.04`).
- [ ] `AGENTS.md`: amend web invariant 7 with the sanctioned app exception; add the privileged-install invariants (root-owned code, `web *` only, `visudo` before install, uninstall rule-first); layout entries for `npm/`, `install-app.sh`, `install-privileged.sh`; commands; test count.
- [ ] `README.md`: npm install path, menu-bar app section (setup, FDA, start/stop, uninstall), honest verified-vs-not.
- [ ] `site/index.html`: fourth install option (npm), app now seamless (remove "not yet built" claims), design section gains the privilege model paragraph, audience updated.
- [ ] Spec + plan committed.

## WS-5 — review and verification

- [ ] Reviewer (read-only): contract adherence, sudoers rule exactness, root-ownership checks, no new invariant weakening, no `osascript`, app lifecycle correctness (no orphan server, no double start), npm recursion guard, workflow permissions, doc honesty.
- [ ] Tester: `uv run pytest -q`; `npm test`; `npm pack --dry-run`; `bash -n` on all scripts; `install-privileged.sh --check`; `build-app.sh` + bundle checks; site anchor/link check; YAML parse.
- [ ] Integrator: fix findings, update counts, commit in chunks, push, PR.

## Verification commands

```bash
cd ~/workspace/repos/ntfs-mounter
uv run pytest -q
(cd npm && npm test && npm pack --dry-run)
bash -n scripts/install-privileged.sh scripts/install-app.sh scripts/build-app.sh
bash scripts/install-privileged.sh --check
bash scripts/build-app.sh
```

## Manual acceptance (owner, needs a password)

1. `sudo ./scripts/install-privileged.sh` in the Full Disk Access terminal.
2. `bash scripts/install-app.sh --launch`.
3. Menu bar → **Start dashboard** → browser opens, no password.
4. Mount/write/unmount from the web UI.
5. **Quit** → `pgrep -f "ntfsmnt web"` is empty.
6. `sudo ./scripts/install-privileged.sh --uninstall` → rule and venv gone.
