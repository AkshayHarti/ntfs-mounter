# Releasing ntfsmnt

Releases are tokenless: pushing a `vX.Y.Z` tag builds the sdist and wheel and
publishes them to PyPI over OIDC trusted publishing. There are no PyPI
passwords or API tokens in this repository or its secrets.

## 1. One-time: add the PyPI pending publisher

On <https://pypi.org> → **Your projects** → **Publishing** → **Add a pending
publisher**, enter exactly:

| Field | Value |
|---|---|
| PyPI project name | `ntfsmnt` |
| Owner | `AkshayHarti` |
| Repository name | `ntfs-mounter` |
| Workflow name (filename) | `release.yml` |
| Environment name | `pypi` |

Do this once. The first successful release claims the project name and turns the
pending publisher into a normal trusted publisher.

`ntfsmnt` was unclaimed on PyPI when this document was written (2026-10-23).
If the form rejects the name, someone claimed it in the meantime: stop and pick
a different distribution name before publishing.

## 2. Bump the version

Edit **only** `ntfsmnt/__init__.py`:

```python
__version__ = "0.1.1"
```

`pyproject.toml` uses a dynamic version read from that attribute, so this file is
the single source of truth. Do not add or edit a `version =` line in
`pyproject.toml`.

## 3. Verify locally

```sh
uv run pytest -q
uv build
```

`uv build` must produce `dist/ntfsmnt-<version>.tar.gz` and
`dist/ntfsmnt-<version>-py3-none-any.whl` matching the version you just set.

## 4. Tag and push

```sh
git add ntfsmnt/__init__.py
git commit -m "release: vX.Y.Z"
git push origin main
git tag vX.Y.Z
git push origin vX.Y.Z
```

Use the real version in place of `X.Y.Z`. The tag must be `v` + the exact
`__version__` string.

## 5. Confirm the release

- The `release` workflow run for the tag is green (build job, then publish job).
- <https://pypi.org/project/ntfsmnt/> shows the new version.
- A clean install works:

```sh
uv tool install "ntfsmnt[web]"
```

## 6. Rollback

- **Wrong or broken release:** yank it on PyPI (project → Manage → Releases →
  Yank). Yanking hides it from new installs without deleting it. Then publish a
  patch version; anyone who already installed the yanked version must upgrade
  explicitly.
- **Never move or reuse a tag.** PyPI uploads are immutable and the tag is the
  record of what was published. Fix the problem and publish a patch version
  (`X.Y.Z+1`) with a new tag.
- **The run failed before publishing:** fix the cause and push a new patch tag;
  do not delete and re-push the old one.

## Failure by design: tag/version mismatch

The `build` job reads the version from the package and fails when
`v$VERSION != $GITHUB_REF_NAME`:

```
tag v0.1.2 does not match package version v0.1.1
```

That is deliberate — it stops a tag from publishing a different version than it
names. Fix `ntfsmnt/__init__.py`, commit, and push a new tag; do not retag.
