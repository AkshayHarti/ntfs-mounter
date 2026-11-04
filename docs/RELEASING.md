# Releasing ntfsmnt

Releases are tokenless: pushing a `vX.Y.Z` tag builds the sdist and wheel and
publishes them to PyPI over OIDC trusted publishing. There are no PyPI
passwords or API tokens in this repository or its secrets. The npm package uses
the same model after a one-time bootstrap (section 4).

## 1. Current state

- `0.1.0` is **live on PyPI** (<https://pypi.org/project/ntfsmnt/>). It claimed
  the `ntfsmnt` project name and the pending publisher configured for this
  repository, so that trusted publisher is now a normal one and already works.
  There is nothing to add on PyPI.
- The trusted publisher is bound to owner `AkshayHarti`, repository
  `ntfs-mounter`, workflow `release.yml`, environment `pypi`. Do not rename any
  of those without updating the publisher.
- **`0.2.0` is the next release**, because `0.1.0` already exists on PyPI and a
  version number can never be reused. `ntfsmnt/__init__.py`,
  `npm/package.json`, and `server.json` are already at `0.2.0`.
- The npm package name is **not** published yet; section 4 bootstraps it once.

## 2. Bump the version

Three files carry the version and **all three must match**:

| File | Field | Role |
|---|---|---|
| `ntfsmnt/__init__.py` | `__version__` | source of truth |
| `npm/package.json` | `version` | the npm channel |
| `server.json` | top-level `version` and `packages[].version` | the MCP registry entry |

`Formula/ntfsmnt.rb` is outside that rule but is also version-pinned: it names
the sdist URL and `sha256` of a *published* version (still `0.1.0`), so it
needs its own update after the PyPI release; see `docs/HOMEBREW.md`.

`pyproject.toml` uses a dynamic version read from `ntfsmnt/__init__.py`, so do
not add or edit a `version =` line there. Set it in `ntfsmnt/__init__.py`:

```python
__version__ = "0.2.0"
```

Then mirror it in `npm/package.json`:

```json
"version": "0.2.0"
```

and in both `version` fields of `server.json` (top level and the `pypi` entry
in `packages`):

```json
"version": "0.2.0"
```

`tests/test_npm_package.py` asserts that the npm and `server.json` versions
equal `ntfsmnt.__version__`; `tests/test_server_json.py` covers the registry
manifest. A mismatch fails the suite before the tag exists.

## 3. Verify locally

```sh
uv run pytest -q
uv build
```

`uv build` must produce `dist/ntfsmnt-<version>.tar.gz` and
`dist/ntfsmnt-<version>-py3-none-any.whl` matching the version you just set.

## 4. npm first-publish bootstrap (one time)

CI cannot create the npm package name: OIDC trusted publishing only works for a
package that already exists. Run this once, in order. It needs **npm >= 11.5.1**
and an account with **2FA** enabled.

a. Log in interactively, from the repo root:

   ```sh
   npm login
   ```

b. From a clean checkout of the release commit on `main` (nothing staged or
   modified), publish a throwaway prerelease so the name exists. Run this after
   the release commit is pushed and **before** the `vX.Y.Z` tag is pushed (see
   section 7). Set the npm version temporarily, publish it, then restore the
   real version:

   ```sh
   cd npm
   npm version 0.2.0-bootstrap.0 --no-git-tag-version
   npm publish --tag bootstrap --access public
   npm version 0.2.0 --no-git-tag-version
   cd ..
   git status --short   # must be clean
   ```

   `--tag bootstrap` keeps `latest` free for the real release. Never publish
   `0.2.0` by hand.

c. Bind the package to this repository's npm workflow:

   ```sh
   npm trust github ntfsmnt --file npm.yml \
     --repo AkshayHarti/ntfs-mounter --allow-publish -y
   ```

d. Thereafter CI publishes real versions on `v*` tags
   (`.github/workflows/npm.yml`). That workflow **must not** pass
   `registry-url` to `actions/setup-node`: it writes an empty `_authToken` into
   `.npmrc`, which shadows OIDC trusted publishing and fails `npm publish` with
   `ENEEDAUTH`. The workflow carries a comment saying so; keep it.

## 5. Demo image asset (`demo-v1`)

`ntfsmnt demo` downloads `demo.img` from a GitHub release, verifies the pinned
SHA-256, and attaches it read-only. The image is built once and is not rebuilt
per release. From the repo root, with Docker running:

```sh
mkdir -p dist
docker run --rm -v "$PWD/dist:/out" alpine:3 sh -euxc '
  apk add --no-cache ntfs-3g-progs
  dd if=/dev/zero of=/tmp/demo.img bs=1M count=16
  mkfs.ntfs -F -L NTFSDEMO /tmp/demo.img
  cp /tmp/demo.img /out/demo.img
'
shasum -a 256 dist/demo.img
```

Upload it as release **`demo-v1`** with asset name **`demo.img`**:

```sh
gh release create demo-v1 dist/demo.img \
  --title "Demo disk image (NTFSDEMO)" \
  --notes "A 16 MiB NTFS image for ntfsmnt demo. Read-only and disposable."
```

The `demo-v1` tag deliberately does not match the `v*` pattern, so it triggers
neither `release.yml` nor `npm.yml`.

Then pin the digest printed by `shasum -a 256` in `ntfsmnt/demo.py` and commit:

```python
DEMO_SHA256 = "8713b7aceac000c8eaf6dd35aca63373b605bb6dd7eccf2890c24a5925d9aba4"
```

A new image means a new release tag (`demo-v2`) and a new hash; never overwrite
an asset a released version has pinned. While `DEMO_SHA256` is empty,
`ntfsmnt demo` refuses instead of attaching an unverified image. A download
whose hash does not match is deleted and the command fails.

## 6. Publish to the MCP registry

`server.json` is the registry manifest. Publishing needs two things in place:

- the version in `server.json` is live on PyPI (section 7), because the `pypi`
  package entry points at that exact version.
- `README.md` contains the ownership marker
  `<!-- mcp-name: io.github.akshayharti/ntfsmnt -->`; the registry reads it to
  prove the README belongs to the server named in `server.json`.

From the repo root:

```sh
mcp-publisher login github
mcp-publisher validate
mcp-publisher publish
```

`mcp-publisher validate` **exits 0 even when validation fails**. Read its
output; do not trust the exit code alone.

## 7. Tag and push

```sh
git add ntfsmnt/__init__.py npm/package.json server.json
git commit -m "release: vX.Y.Z"
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

Use the real version in place of `X.Y.Z`. The tag must be `v` + the exact
`__version__` string; both the `release` and `npm` workflows assert that before
publishing.

The npm bootstrap in section 4, if it is still needed, runs after
`git push origin main` and before `git tag vX.Y.Z`.

## 8. Confirm the release

- The `release` workflow run for the tag is green (build job, then publish job),
  and so is the `npm` workflow run.
- <https://pypi.org/project/ntfsmnt/> shows the new version.
- npm shows the same version, and a clean install works:

```sh
npm view ntfsmnt version
uv tool install "ntfsmnt[web]"
```

- The registry listing for `io.github.akshayharti/ntfsmnt` shows the new
  version after section 6.

## 9. Menu-bar app signing (optional)

`docs/APP.md` → **Signing and notarizing** has the full steps. It is optional
and currently **blocked**: it needs an Apple Developer ID Application
certificate and notarytool credentials (a keychain profile named
`AC_PASSWORD`). Until those exist, `scripts/build-app.sh` ad-hoc signs the
bundle for local use, and there is no notarized download.

## 10. Rollback

- **Wrong or broken PyPI release:** yank it on PyPI (project → Manage →
  Releases → Yank). Yanking hides it from resolution but does not free the
  version; fix forward with the next patch version.
- **Wrong or broken npm release:** `npm deprecate ntfsmnt@<version> "<reason>"`
  and publish the fix as the next patch. Published npm versions are immutable.
- **Broken MCP registry entry:** fix `server.json`, bump the version, and run
  section 6 again after the matching PyPI release is live.
- **A bad tag:** delete it locally and on the remote
  (`git tag -d vX.Y.Z && git push origin :refs/tags/vX.Y.Z`) only if the
  workflows did not publish. If they did, yank/deprecate instead: the version
  is spent.
- **Failure by design:** the release job publishes nothing if the tag and
  `__version__` disagree, and the npm job publishes nothing if the tag and
  `npm/package.json` disagree. That is the guard working, not a bug to work
  around.

## 11. Pre-release acceptance

Before announcing a release, run `tests/manual/acceptance.md`. It exercises the
real stack on a real NTFS disk, which needs hardware, root, and a terminal with
Full Disk Access. Unit tests inject a fake runner and cannot substitute.
