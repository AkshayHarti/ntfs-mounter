# The npm channel

`ntfsmnt` is published to npm as a **thin proxy**, not a second
implementation. The package is one dependency-free CommonJS bin: it locates the
real Python CLI, runs it with `stdio: "inherit"`, forwards
`SIGINT`/`SIGTERM`/`SIGHUP` to it, and exits with the child's status — or dies
by the same signal when the child dies from one, so a shell sees the usual
`128 + n` as a signal death. All behaviour — mounting, unmounting, the doctor,
the web UI — stays in the Python package.

> The first npm publish happens on the next `v*` tag. Until then `npx ntfsmnt`
> has nothing to fetch; from a checkout use `npm link ./npm` to test the
> wrapper.

## Use

```sh
npx ntfsmnt list                  # one-off, no global install
npm install -g ntfsmnt            # then: ntfsmnt list
ntfsmnt --help
```

The proxy needs a real CLI on the machine. Either install it:

```sh
uv tool install "ntfsmnt[web]"
```

or keep `uvx` on `PATH`; the wrapper then runs
`uvx --from "ntfsmnt[web]" ntfsmnt <args>` on demand (the first run downloads
the package).

## Resolution order

1. `$NTFSMNT_CANDIDATES` — colon-separated; when set it **replaces** steps 2–6
   (a custom prefix, and the hermetic test suite).
2. `$NTFSMNT_BIN` — must exist and be executable.
3. `/usr/local/libexec/ntfsmnt/bin/ntfsmnt` — the root-owned install from
   `scripts/install-privileged.sh`.
4. `/opt/homebrew/bin/ntfsmnt` — the Homebrew formula.
5. `/usr/local/bin/ntfsmnt`.
6. `~/.local/bin/ntfsmnt`.
7. Each `PATH` entry's `ntfsmnt`.

Any candidate whose `realpath` equals the wrapper's own `realpath` is skipped,
so a global npm install cannot re-exec itself. A spawned child also carries
`NTFSMNT_PROXY_ACTIVE=1`, so a second copy of the wrapper (a duplicate global
install) fails with a message instead of bouncing the call back and forth. If
nothing is found the wrapper tries `uvx`, and if `uvx` is absent it prints the
three install routes (checkout + `scripts/setup.sh`, `uv tool install`,
Homebrew tap) and exits 1.

On a platform other than `darwin` the wrapper prints
`ntfsmnt: macOS only (this is <platform>)` and exits 1, unless
`$NTFSMNT_BIN` or `$NTFSMNT_CANDIDATES` is set — those overrides exist so the
test suite can run on Linux CI.

The wrapper uses `spawn`, never a shell, so paths containing spaces are safe.
It has no `dependencies` and no `devDependencies`; `npm test` uses the built-in
`node:test` runner.

## One-time: add the npm trusted publisher

Publishing is tokenless, like the PyPI release. There is no `NPM_TOKEN` and no
secret in this repository.

On <https://www.npmjs.com> → the `ntfsmnt` package → **Settings** → **Trusted
Publisher** → **GitHub Actions**, enter exactly:

| Field | Value |
|---|---|
| Organization or user | `AkshayHarti` |
| Repository | `ntfs-mounter` |
| Workflow filename | `npm.yml` |
| Environment | *(leave empty)* |

The first publish claims the name; after that the trusted publisher is normal
configuration. `npm` was unclaimed when this document was written (2026-10-24);
if the registry rejects the name, someone claimed it in the meantime — stop and
choose a different name before publishing.

## Version sync

`npm/package.json`'s `version` must equal `ntfsmnt.__version__` in
`ntfsmnt/__init__.py`. Bump both in the same commit; `tests/test_npm_package.py`
fails when they diverge, and the workflow refuses a tag that does not match the
npm version.

A `vX.Y.Z` tag runs both channels: `release.yml` publishes to PyPI and `npm.yml`
publishes to npm. `npm.yml` can also be dispatched manually, which runs the
tests only (the publish step is tag-gated).

## Verify locally

```sh
cd npm && npm test        # node:test, no install step
npm pack --dry-run        # bin/, README.md, package.json only
```

`tests/test_npm_package.py` additionally checks the version sync, the absence of
dependencies, the shebang, the `files` coverage of `bin`, that the wrapper
contains no `osascript`, and that the workflow is OIDC-based (`id-token: write`,
`--provenance`, no token, no `secrets.`).

## Rollback

- **Bad release, within 72 hours:** `npm unpublish ntfsmnt@<version>`. npm only
  allows unpublishing a version that is not depended on by other packages and
  that is younger than 72 hours.
- **After 72 hours:** deprecate it instead —
  `npm deprecate ntfsmnt@<version> "broken, use <version>"` — and publish a
  patch. Deprecation leaves the version installable by exact pin, which is the
  honest outcome; never try to rewrite a published version.
- **Workflow failed before publishing:** fix the cause and push a new patch tag;
  do not move or reuse a tag.

## What is not verified

The npm publish itself has not been exercised: the trusted publisher is not
configured yet, and the name was still unclaimed on npm when this was written.
The wrapper's proxy behaviour, resolution order, recursion guard, and signal
forwarding are covered by `npm/test/proxy.test.js`; the npm-only failure mode
(a wrong trusted-publisher field) can only be observed on the first tagged
release.
