# Seamless app + npm channel — design

**Date:** 2026-10-24
**Status:** approved for implementation (decisions below were chosen by the owner)

## Problem

Two frictions, both real:

1. **Install/launch.** The menu-bar app only exists in `dist/` after a source build. There is no `/Applications` install, no prerequisite detection, and no guided setup.
2. **Every run needs Terminal + a password.** The app is a launcher that opens a `.command`; the user types the sudo password in Terminal each time. That is correct security, but it is not "seamless".

Plus a requested third distribution channel: an npm package.

## Decisions

- **Privilege model: on-demand root via a narrow sudoers rule.** One admin install puts a root-owned copy of the CLI at `/usr/local/libexec/ntfsmnt` and grants `NOPASSWD` for **the `web` subcommand only**. The app starts that server itself, reads the token URL from stdout, opens the browser, and kills the server on quit. No Terminal, no password after setup, nothing always-on.
- **npm: thin proxy.** A dependency-free `ntfsmnt` package on npm whose bin finds the real Python CLI and proxies argv/exit codes, falling back to `uvx --from "ntfsmnt[web]"`. No logic is duplicated.
- **No LaunchDaemon, no XPC helper, no notarization** in this change. Those remain future options; the XPC route needs a Developer ID.

## Contract (frozen; both workstreams build against it)

| Item | Value |
|---|---|
| Root-owned CLI | `/usr/local/libexec/ntfsmnt/bin/ntfsmnt` |
| Sudoers file | `/etc/sudoers.d/ntfsmnt`, mode `0440`, owner `root:wheel` |
| Sudoers rule | `%admin ALL=(root) NOPASSWD: /usr/local/libexec/ntfsmnt/bin/ntfsmnt web *` |
| Installer | `scripts/install-privileged.sh` (`--check`, `--uninstall`, `--version <x.y.z>`) |
| App start command | `sudo -n /usr/local/libexec/ntfsmnt/bin/ntfsmnt web --port 0 --owner <uid>:<gid>` |
| App reads | stdout line matching `^ntfsmnt web: (http://\S+)$` |
| App detection | `sudo -n -l /usr/local/libexec/ntfsmnt/bin/ntfsmnt web --port 0 --owner <uid>:<gid>` exits 0 (the full argv: the rule carries arguments, so a bare `web` never matches) |
| npm package | name `ntfsmnt`, bin `ntfsmnt`, version = `ntfsmnt.__version__` |

## Privileged install

`scripts/install-privileged.sh`:

1. Requires root (`sudo`); refuses otherwise with the exact command.
2. Resolves a Python ≥3.12 (`uv` preferred: `uv venv --python 3.12`; else a `python3.12+` on PATH).
3. Builds the venv at `/usr/local/libexec/ntfsmnt` from PyPI (`ntfsmnt[web]==<version>`, version read from the checkout when present, `--version` to override, `--sdist <path>` for local testing).
4. `chown -R root:wheel`, `chmod -R go-w`; refuses to write a rule for a path that is group/other-writable.
5. Writes the sudoers file with `visudo -cf` validation before install, `install -m 0440 -o root -g wheel`.
6. `--check`: read-only; verifies venv, ownership, mode, rule text, `sudo -n -l` — exits 0 anywhere (like `setup.sh --check`).
7. `--uninstall`: removes the rule and the venv, in that order (rule first, so no window where the binary is gone but the grant exists).

**Why the copy is root-owned:** a `NOPASSWD` rule pointing at a user-writable path is passwordless root for anyone who can edit that path. The installer refuses user-writable targets.

## App

`app/MenuBar.swift` becomes a real controller:

- **Setup state.** If detection fails, the menu shows **Set up…**; it opens a bundled `install-privileged.command` (copied into `Contents/Resources` by `build-app.sh`) so Terminal runs the installer with one password prompt.
- **Start dashboard.** Spawns the contract command with `Process`, parses the URL line, opens it with `NSWorkspace`. Menu becomes **Open dashboard** / **Stop dashboard**.
- **Stop / Quit** terminate the child with `SIGTERM` and wait briefly, then `SIGKILL` only if it is still alive after 5 s (this is process cleanup of our own child, not the unmounter's disk logic).
- **Full Disk Access.** The root server inherits the app's TCC attribution, so the app needs FDA once. A menu item opens the FDA pane (`x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles`); the app detects mount failure with `EPERM` and points at it.
- **Doctor** runs the root-owned CLI unprivileged (read-only checks).
- `scripts/install-app.sh` builds and copies the bundle to `/Applications` (or `~/Applications` with `--user`), clears quarantine, optionally launches.

## npm package

```
npm/
  package.json      name ntfsmnt, bin, files, engines >=18, no dependencies
  bin/ntfsmnt.js    resolution + proxy
  README.md
  test/proxy.test.js  node:test, no deps
```

Resolution order: `NTFSMNT_BIN` → root-owned copy → `/opt/homebrew/bin` → `/usr/local/bin` → `~/.local/bin` → every PATH entry (skipping the wrapper's own realpath, so `npx ntfsmnt` cannot recurse) → `uvx --from "ntfsmnt[web]" ntfsmnt`.

Proxy: `stdio: inherit`, forward `SIGINT`/`SIGTERM`/`SIGHUP`, exit with the child's code or `128 + signal`. If nothing is found, print the three install paths and exit 1.

Publishing: `.github/workflows/npm.yml` on `v*` tags, `actions/setup-node@v7` with `registry-url`, `id-token: write`, `npm publish --provenance --access public` from `npm/`. Requires a one-time trusted publisher on npmjs.com (documented, like the PyPI one).

## Security analysis

- **Standing grant.** `NOPASSWD` for `web *` on a root-owned binary means any admin user can start a root web server bound to loopback. The web layer already enforces token→cookie, `X-NTFSMNT` + Origin, Host allow-list, and a single operation lock; this change does not weaken those. It is disclosed in the docs and opt-in at install.
- **No new network surface.** The server still binds `127.0.0.1` only, on a random port, and dies with the app.
- **Token never touches disk.** It goes from the server's stdout pipe to `NSWorkspace`.
- **Uninstall order** removes the grant before the binary.
- **TCC.** FDA moves from Terminal to the app for app-started sessions. Terminal-based use (`sudo ntfsmnt web`) still works and still uses Terminal's FDA.

## Invariant change (explicit)

`AGENTS.md` web invariant 7 says "Nothing re-execs `sudo` on the user's behalf". The app now does exactly that, by design, through the narrow rule. Amend it to name the exception and add: root-owned code only, rule restricted to `web`, installer validates with `visudo`, uninstall removes the rule first.

## Non-goals

LaunchDaemon/always-on server, XPC privileged helper, notarization, launch-at-login, Linux/Windows, a Node reimplementation of the web UI.

## Acceptance

- `scripts/install-privileged.sh --check` exits 0 unprivileged and reports each prerequisite.
- Installer static tests: `bash -n`, `visudo -cf` before install, `0440 root:wheel`, rule contains only `web *`, refuses non-root, refuses a writable target, `--uninstall` removes the rule first.
- App builds; bundle contains the installer; no `osascript`; start command and URL regex match the contract; stop sends `SIGTERM` first.
- npm: `npm test` in `npm/` passes with a fake CLI (args, exit code, missing-binary message, recursion guard); `npm pack --dry-run` contains only the four files; version equals `ntfsmnt.__version__`.
- Live (owner, requires password): run the installer, click **Start dashboard**, confirm the browser opens with no password and the server dies on quit.
