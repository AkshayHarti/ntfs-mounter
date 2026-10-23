# Homebrew tap (Formula/)

This repo is also a Homebrew tap: `Formula/` is tapped by URL with

```bash
brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter
brew install AkshayHarti/ntfs-mounter/ntfsmnt
```

The repo name matters: a GitHub tap is normally `<user>/homebrew-<repo>`, but
tap and project live in the same repository here, so `brew tap` must get the
full URL.

On Homebrew 7+ third-party taps must be trusted before their formulae load.
When `brew install` fails with a trust warning, run:

```bash
brew trust --tap AkshayHarti/ntfs-mounter
```

## The two formulae

### `ntfs-3g-fuse-t` — the FUSE-T-linked ntfs-3g

The same port of [macos-fuse-t/ntfs-3g](https://github.com/macos-fuse-t/ntfs-3g)
the setup script builds by hand:

- `url` is the GitHub tarball of a pinned commit of the fork's `edge` branch
  (`f0e5cb0274e30334dd03aefb4fd5a14c239e6756`), so the build is reproducible.
  `version` is upstream's own `2022.10.3` plus the first 8 characters of the
  sha, because the fork has no release tag. Update both together.
- `depends_on` lists `autoconf`, `automake`, `libtool`, `pkgconf` as build deps
  (the tarball has no generated `./configure`), and `ntfs-3g.probe` plus the
  CLI are built by `make`.
- `--disable-ntfsprogs` skips `mkntfs` and friends. `ntfs-3g.probe` is built in
  `src/` under `ENABLE_NTFS_3G`, not in `ntfsprogs/`, so it is kept.
- `conflicts_with "ntfs-3g-mac"`: both install an `/opt/homebrew/bin/ntfs-3g`,
  one linked against macFUSE and one against FUSE-T. While
  `gromgit/fuse/ntfs-3g-mac` is installed, this formula refuses to install.

Build adjustments that were found against Homebrew 7.0.10 on Apple Silicon:

- `--exec-prefix=#{prefix}` — without it configure sets `rootlibdir=/lib` and
  the `libntfs-3g` install hook tries to `mv` `libntfs-3g.so*` into `/lib`,
  which does not exist on macOS.
- `ENV.append_path "CPATH", "/usr/local/include/fuse"` — Homebrew's superenv
  drops `-I` paths outside the Homebrew prefix, which silently removes the
  `-I/usr/local/include/fuse` the fork's `src/Makefile.am` hardcodes.
- `ENV.append "LDFLAGS", "-Wl,-rpath,/usr/local/lib -Wl,-L/usr/local/lib"` —
  FUSE-T's dylib install name is `@rpath/libfuse-t.dylib`, so the binary needs
  the rpath; plain `-L/usr/local/lib` is equally dropped by superenv, hence the
  `-Wl,-L` spelling (also not filtered by superenv).
- Homebrew 7 removed `depends_on cask:` from the formula DSL (`brew tap`
  rejects it with `Unsupported special dependency: :cask`), so the FUSE-T cask
  is a fatal inline `FuseTRequirement` that checks
  `/usr/local/lib/libfuse-t.dylib` and prints `brew install --cask fuse-t`.
  The cask is *not* installed automatically; that is a real difference from
  the plan, not a cosmetic one.

### `ntfsmnt` — the CLI

A standard virtualenv formula: the PyPI sdist of `ntfsmnt 0.1.0` with
`python@3.13`, the `web` extra's dependency closure (fastapi, uvicorn,
jinja2 and their transitive dependencies) as resources, and
`depends_on "ntfs-3g-fuse-t"`, which makes the FUSE-T requirement
transitive. The resource blocks were generated with
`brew update-python-resources --ignore-main-package-cooldown
--extra-packages "fastapi,uvicorn,jinja2"`; rebuilding `pydantic-core` needs
`maturin` and `rust`, the same build dependencies homebrew-core's own
`pydantic` formula declares, plus `allow_network_access! :build` for cargo.

## How this differs from `scripts/setup.sh`

| | `scripts/setup.sh` | tap formulae |
|---|---|---|
| What is installed | FUSE-T cask + ntfs-3g from source; the CLI must be installed separately | FUSE-T cask (required, not installed) + ntfs-3g + the CLI + web UI |
| Where ntfs-3g lands | `/usr/local/bin/ntfs-3g` (via `sudo make install`) | `/opt/homebrew/Cellar/…/bin`, symlinked into `/opt/homebrew/bin` |
| Which ntfs-3g build | tip of `edge` (`NTFS3G_REF` can pin it) | one pinned commit |
| ntfsprogs utilities | built and installed | skipped (`--disable-ntfsprogs`) |
| Root? | `sudo make install` | never (Homebrew sandbox); the cask installer asks for a password itself |

On a machine that used `scripts/setup.sh`, resolution still picks
`/usr/local/bin/ntfs-3g` first (`ntfsmnt/paths.py`), so installing the tap
formula does not change which binary the CLI uses. A mixed machine keeps the
honest behaviour from that module: if a macFUSE-linked `ntfs-3g` ends up
being resolved, `doctor` reports it as not linked against FUSE-T; a Homebrew
prefix does not hide it, and `NTFSMNT_NTFS3G` overrides everything.

## Local tap testing

```bash
rm -rf /tmp/ntfsmnt-tap
git clone https://github.com/AkshayHarti/ntfs-mounter /tmp/ntfsmnt-tap
# Formula/ must be committed in the clone — `git clone` of a working tree only
# copies tracked files, so new formulas disappear silently otherwise:
cd /tmp/ntfsmnt-tap && git add Formula && git commit -m "local tap test"
brew tap AkshayHarti/ntfs-mounter /tmp/ntfsmnt-tap
brew trust --tap AkshayHarti/ntfs-mounter
brew audit --strict akshayharti/ntfs-mounter/ntfsmnt
brew install --build-from-source AkshayHarti/ntfs-mounter/ntfsmnt
brew test AkshayHarti/ntfs-mounter/ntfsmnt
```

`brew untap --force AkshayHarti/ntfs-mounter` removes the tap (and uninstalls
formulae installed from it).

## What was verified (Homebrew 7.0.10, macOS 26.5.2 Apple Silicon)

- `brew style --fix Formula/` clean; `brew audit --strict` on both formulae
  clean after the fixes below.
- `ntfs-3g-fuse-t` builds from source into its Cellar keg. The keg's
  `bin/ntfs-3g --version` reports `ntfs-3g 2022.10.3 external FUSE 29`,
  `otool -L` shows `@rpath/libfuse-t.dylib` and `otool -l` shows
  `LC_RPATH /usr/local/lib`, and `ntfs-3g.probe` exists.
- `brew install AkshayHarti/ntfs-mounter/ntfsmnt` *fails by design* on this
  machine: it refuses because conflicting formula `ntfs-3g-mac` is installed.
  With the conflicting formula temporarily out of the picture (test install
  with `--force`/`--skip-link`, without touching `ntfs-3g-mac`), `ntfsmnt`
  builds and `brew test` passes; the venv ships `fastapi`, `uvicorn`, and
  `jinja2` importable, and `/opt/homebrew/bin/ntfsmnt --help` exits 0.
- `ntfs-3g-fuse-t` was not linked while the conflict stands, so
  `brew test AkshayHarti/ntfs-mounter/ntfs-3g-fuse-t` cannot be run here; the
  keg binary itself was exercised instead (see above).
- A real root mount through the Homebrew-produced `ntfs-3g` was not exercised
  (no bare-block hardware mounted for this change).

## Uninstall

```bash
brew uninstall AkshayHarti/ntfs-mounter/ntfsmnt
brew uninstall AkshayHarti/ntfs-mounter/ntfs-3g-fuse-t
brew untap AkshayHarti/ntfs-mounter            # only removes the tap itself
```

Do not `brew uninstall --cask fuse-t` while any FUSE-T mount is live; see the
README's Uninstall section.
