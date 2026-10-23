#!/usr/bin/env bash
# Install the non-Python prerequisites for ntfsmnt on macOS.
#
#   scripts/setup.sh [--check] [--yes]
#
# --check  read-only: report each prerequisite, always exit 0 (usable from CI).
# --yes    do not prompt before the install steps (sudo still asks for a password).
#
# Run this as your user, never with sudo. It calls sudo only for the final
# `make install` of ntfs-3g; the FUSE-T cask asks for an administrator password
# through its own installer.
set -euo pipefail

FUSE_T_DYLIB=/usr/local/lib/libfuse-t.dylib
NTFS3G_BIN=/usr/local/bin/ntfs-3g
NTFS3G_REPO=https://github.com/macos-fuse-t/ntfs-3g.git
NTFS3G_REF=${NTFS3G_REF:-edge}
CACHE_ROOT=${XDG_CACHE_HOME:-$HOME/Library/Caches}/ntfsmnt
NTFS3G_SRC=$CACHE_ROOT/ntfs-3g
BUILD_TOOLS=(autoconf automake libtool pkgconf)

usage() {
  cat <<'EOF'
usage: scripts/setup.sh [--check] [--yes]

  --check  report the prerequisites without changing anything (always exits 0)
  --yes    skip the confirmation prompt before the install steps
EOF
}

say() { printf '%s\n' "$*"; }
ok() { printf 'ok: %s\n' "$*"; }
missing() { printf 'MISSING: %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }

have() { command -v "$1" >/dev/null 2>&1; }

links_fuse_t() { otool -L "$NTFS3G_BIN" 2>/dev/null | grep -q libfuse-t; }

run_check() {
  local uname_s
  uname_s=$(uname -s)

  if have uv; then
    ok "uv on PATH ($(uv --version 2>/dev/null || say 'version unknown'))"
  else
    missing "uv on PATH — install from https://docs.astral.sh/uv/"
  fi

  if have brew; then
    ok "Homebrew on PATH ($(brew --version 2>/dev/null | head -1))"
  else
    missing "Homebrew on PATH — https://brew.sh/ (installs FUSE-T and the build tools)"
  fi

  if have git; then
    ok "git on PATH ($(git --version))"
  else
    missing "git on PATH — fetches macos-fuse-t/ntfs-3g"
  fi

  if [[ $uname_s != Darwin ]]; then
    warn "uname -s = $uname_s; macOS is required, so the FUSE-T and ntfs-3g checks were skipped"
    return 0
  fi
  ok "macOS (Darwin $(uname -r))"

  if [[ -f $FUSE_T_DYLIB ]]; then
    ok "FUSE-T at $FUSE_T_DYLIB"
  else
    missing "FUSE-T at $FUSE_T_DYLIB — brew install --cask fuse-t"
  fi

  if [[ -x $NTFS3G_BIN ]]; then
    ok "ntfs-3g at $NTFS3G_BIN"
    if have otool; then
      if links_fuse_t; then
        ok "$NTFS3G_BIN links libfuse-t"
      else
        warn "$NTFS3G_BIN is not linked against libfuse-t — rerun scripts/setup.sh to rebuild it"
      fi
    else
      warn "otool not found; skipped the libfuse-t link check"
    fi
  else
    missing "ntfs-3g at $NTFS3G_BIN — run scripts/setup.sh to build macos-fuse-t/ntfs-3g"
  fi
  return 0
}

require_brew() {
  if ! have brew; then
    say "error: Homebrew is required to install $1 — see https://brew.sh/" >&2
    exit 1
  fi
}

MODE=install
ASSUME_YES=0
for arg in "$@"; do
  case $arg in
    --check) MODE=check ;;
    --yes) ASSUME_YES=1 ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ $MODE == check ]]; then
  run_check
  exit 0
fi

if [[ $EUID -eq 0 ]]; then
  say "error: run this script as your user, not with sudo; it calls sudo only for 'make install'." >&2
  exit 1
fi

if [[ $(uname -s) != Darwin ]]; then
  say "error: macOS is required (uname -s = $(uname -s))." >&2
  exit 2
fi

need_fuse_t=0
[[ -f $FUSE_T_DYLIB ]] || need_fuse_t=1

need_tools=()
for tool in "${BUILD_TOOLS[@]}"; do
  have "$tool" || need_tools+=("$tool")
done

# Rebuild when the binary is absent or was linked against something else.
need_ntfs3g=1
if [[ -x $NTFS3G_BIN ]]; then
  if ! have otool || links_fuse_t; then
    need_ntfs3g=0
  fi
fi

if ((need_fuse_t + ${#need_tools[@]} + need_ntfs3g == 0)); then
  say "All prerequisites are already installed."
else
  step "Plan"
  if ((need_fuse_t)); then
    say "  - install the FUSE-T cask: brew install --cask fuse-t"
    say "    (the cask may ask for your password or an admin approval)"
  fi
  if ((${#need_tools[@]})); then
    say "  - install build tools: brew install ${need_tools[*]}"
  fi
  if ((need_ntfs3g)); then
    say "  - build ntfs-3g ($NTFS3G_REF) from $NTFS3G_REPO into $NTFS3G_SRC"
    say "    and run 'sudo make install' (installs $NTFS3G_BIN)"
  fi
  if ((!ASSUME_YES)); then
    printf 'Proceed? [y/N] '
    read -r reply || reply=
    case $reply in
      [yY] | [yY][eE][sS]) ;;
      *)
        say "Nothing changed."
        exit 0
        ;;
    esac
  fi
fi

if ((need_fuse_t)); then
  step "FUSE-T"
  require_brew "the FUSE-T cask"
  brew install --cask fuse-t
fi

if ((${#need_tools[@]})); then
  step "Build tools"
  require_brew "the build tools"
  brew install "${need_tools[@]}"
fi

if ((need_ntfs3g)); then
  step "ntfs-3g source"
  if ! have git; then
    say "error: git is required to clone $NTFS3G_REPO" >&2
    exit 1
  fi
  mkdir -p "$CACHE_ROOT"
  if [[ -d $NTFS3G_SRC/.git ]]; then
    say "Updating $NTFS3G_SRC"
    git -C "$NTFS3G_SRC" fetch --quiet origin
  else
    say "Cloning $NTFS3G_REPO ($NTFS3G_REF) into $NTFS3G_SRC"
    git clone --quiet "$NTFS3G_REPO" "$NTFS3G_SRC"
  fi
  if git -C "$NTFS3G_SRC" rev-parse --verify --quiet "origin/$NTFS3G_REF" >/dev/null; then
    git -C "$NTFS3G_SRC" checkout --quiet -B "$NTFS3G_REF" "origin/$NTFS3G_REF"
  else
    # NTFS3G_REF may be a commit SHA or a ref that only exists locally.
    git -C "$NTFS3G_SRC" checkout --quiet --detach "$NTFS3G_REF"
  fi
  say "ntfs-3g commit: $(git -C "$NTFS3G_SRC" rev-parse HEAD)"

  step "Build ntfs-3g"
  # The fork hardcodes -I/usr/local/include/fuse and -lfuse-t on macOS, so
  # configure needs no extra flags.
  make_jobs=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)
  (cd "$NTFS3G_SRC" && ./autogen.sh && ./configure && make -j"$make_jobs")

  step "Install ntfs-3g"
  say "Running 'sudo make install'; sudo will ask for your password."
  (cd "$NTFS3G_SRC" && sudo make install)
fi

step "Result"
[[ -f $FUSE_T_DYLIB ]] && ok "$FUSE_T_DYLIB" || warn "FUSE-T not found at $FUSE_T_DYLIB"
[[ -x $NTFS3G_BIN ]] && ok "$NTFS3G_BIN" || warn "ntfs-3g not found at $NTFS3G_BIN"

cat <<'EOF'

One manual step remains — grant Full Disk Access to your terminal:
  System Settings → Privacy & Security → Full Disk Access → enable your terminal
Then quit and reopen the terminal. Without this, sudo still gets EPERM on the
raw disk device.

Next:
  uv tool install "ntfsmnt[web]"
  ntfsmnt doctor
EOF
