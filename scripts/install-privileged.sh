#!/usr/bin/env bash
# Install the root-owned ntfsmnt CLI and the narrow sudoers rule that lets the
# menu-bar app start `ntfsmnt web` without a password.
#
#   sudo scripts/install-privileged.sh [--version <x.y.z>] [--sdist <path>]
#   scripts/install-privileged.sh --check      # read-only, no root, exits 0 anywhere
#                                               # (it does run `sudo -n -l`, never a mutation)
#   sudo scripts/install-privileged.sh --uninstall
#
# Contract: docs/specs/2026-10-24-seamless-app-and-npm.md ("Privileged install").
# A NOPASSWD rule pointing at a user-writable path is passwordless root, so this
# script refuses any path under the venv that is group- or other-writable.
set -euo pipefail

VENV_DIR=/usr/local/libexec/ntfsmnt
CLI_BIN=$VENV_DIR/bin/ntfsmnt
SUDOERS_FILE=/etc/sudoers.d/ntfsmnt
RULE='%admin ALL=(root) NOPASSWD: /usr/local/libexec/ntfsmnt/bin/ntfsmnt web *'
MIN_PY=3.12
PY_NAMES=(python3.12 python3.13 python3.14)

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)
SCRIPT_PATH=$SCRIPT_DIR/$(basename "$0")
INIT_PY=$REPO_ROOT/ntfsmnt/__init__.py
# sudo resets PATH; visudo lives in /usr/sbin and brew/uv in their prefixes, so
# keep all of them reachable.
PATH=$PATH:/usr/sbin:/usr/local/sbin
if [[ $SCRIPT_PATH == "$REPO_ROOT"/* ]]; then
  RUN_HINT=./${SCRIPT_PATH#"$REPO_ROOT"/}
else
  RUN_HINT=$SCRIPT_PATH
fi

OK=0
FAILED=0
INFO=0
TMP_SUDOERS=''
ORIGINAL_ARGS_STR=''

say() { printf '%s\n' "$*"; }
ok() { OK=$((OK + 1)); printf 'ok: %s\n' "$*"; }
fail() { FAILED=$((FAILED + 1)); printf 'FAIL: %s\n' "$*"; }
info() { INFO=$((INFO + 1)); printf 'info: %s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

cleanup() {
  if [[ -n $TMP_SUDOERS ]]; then
    rm -f "$TMP_SUDOERS"
  fi
}
trap cleanup EXIT

usage() {
  cat <<'EOF'
usage: sudo scripts/install-privileged.sh [--version <x.y.z>] [--sdist <path>]
       scripts/install-privileged.sh --check
       sudo scripts/install-privileged.sh --uninstall

  --version <x.y.z>  install this release from PyPI
                     (default: __version__ in ntfsmnt/__init__.py)
  --sdist <path>     install this local wheel/sdist instead of PyPI (testing)
  --check            report the installed state; read-only, exits 0 anywhere
  --uninstall        remove the sudoers rule, then the root-owned CLI
  -h, --help         show this help
EOF
}

# resolve_tool <name> [<absolute candidate>...] -> absolute path on stdout
resolve_tool() {
  local name=$1 candidate found
  shift
  found=$(command -v "$name" 2>/dev/null || true)
  if [[ -n $found ]]; then
    printf '%s' "$found"
    return 0
  fi
  for candidate in "$@"; do
    if [[ -n $candidate && -x $candidate ]]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

find_uv() {
  resolve_tool uv /opt/homebrew/bin/uv /usr/local/bin/uv
}

# find_python -> absolute path of a python3.12+ on stdout
find_python() {
  local name candidate
  for name in "${PY_NAMES[@]}"; do
    candidate=$(resolve_tool "$name" "/opt/homebrew/bin/$name" "/usr/local/bin/$name") || continue
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

require_root() {
  if [[ $EUID -ne 0 ]]; then
    printf 'error: this script must run as root to write %s and %s.\n' "$VENV_DIR" "$SUDOERS_FILE" >&2
    printf '  run: sudo %s%s\n' "$RUN_HINT" "$ORIGINAL_ARGS_STR" >&2
    exit 1
  fi
}

require_macos() {
  local uname_s
  uname_s=$(uname -s)
  if [[ $uname_s != Darwin ]]; then
    printf 'error: macOS is required (uname -s = %s).\n' "$uname_s" >&2
    exit 1
  fi
}

# resolve_spec -> the pip requirement to install (sdist path, or ntfsmnt[web]==x.y.z)
resolve_spec() {
  local version
  if [[ -n $SDIST ]]; then
    [[ -e $SDIST ]] || die "--sdist path not found: $SDIST"
    printf '%s' "$SDIST"
    return 0
  fi
  version=$VERSION
  if [[ -z $version && -f $INIT_PY ]]; then
    version=$(awk -F'"' '/^__version__ = "/ { print $2; exit }' "$INIT_PY")
  fi
  if [[ -z $version ]]; then
    die "no version to install: pass --version <x.y.z> (no $INIT_PY next to this script)"
  fi
  printf 'ntfsmnt[web]==%s' "$version"
}

writable_paths() {
  find "$VENV_DIR" \( -perm -0020 -o -perm -0002 \) -print -quit 2>/dev/null || true
}

# A NOPASSWD target must not sit under a directory an admin user can replace:
# that would make the grant passwordless root for anyone who can write there.
untrusted_ancestors() {
  local dir perms bad=''
  dir=$(dirname "$VENV_DIR")
  while [[ -n $dir && $dir != / ]]; do
    if [[ -d $dir ]]; then
      perms=$(stat -f '%Lp' "$dir")
      if [[ $(stat -f '%Su' "$dir") != root ]] || (( (8#$perms & 0022) != 0 )); then
        bad="$bad $dir"
      fi
    fi
    dir=$(dirname "$dir")
  done
  printf '%s' "${bad# }"
}

require_trusted_ancestors() {
  local bad
  bad=$(untrusted_ancestors)
  if [[ -n $bad ]]; then
    die "refusing to install: not root-owned or group/other-writable:$bad
  A NOPASSWD rule under such a path is passwordless root for anyone who can write there.
  Fix ownership (chown root:wheel) or install on a machine where /usr/local is root-owned."
  fi
}

write_sudoers() {
  local writable
  writable=$(writable_paths)
  if [[ -n $writable ]]; then
    die "refusing to grant NOPASSWD to a writable path: $writable"
  fi

  TMP_SUDOERS=$(mktemp "${TMPDIR:-/tmp}/ntfsmnt-sudoers.XXXXXX")
  printf '%s\n' "$RULE" >"$TMP_SUDOERS"

  step "Validate the rule with visudo"
  if ! visudo -cf "$TMP_SUDOERS"; then
    die "the rule failed validation; $SUDOERS_FILE was left untouched"
  fi
  ok "visudo accepted the rule"

  step "Install $SUDOERS_FILE (0440 root:wheel)"
  install -m 0440 -o root -g wheel "$TMP_SUDOERS" "$SUDOERS_FILE"
  ok "$SUDOERS_FILE written"
}

install_cli() {
  local spec uv python build_dir

  if ! have visudo; then
    die "visudo is required to validate the sudoers rule (part of sudo) and was not found; nothing was changed"
  fi
  spec=$(resolve_spec) || exit 1

  step "Create $(dirname "$VENV_DIR")"
  mkdir -p "$(dirname "$VENV_DIR")"
  require_trusted_ancestors

  # Build beside the target and swap it in, so a failed install never leaves a
  # half-populated venv that the NOPASSWD rule would still point at.
  build_dir=$VENV_DIR.build.$$
  trap "rm -rf '$build_dir'; cleanup" EXIT
  rm -rf "$build_dir"

  if uv=$(find_uv); then
    say "using uv at $uv"
    "$uv" venv --python "$MIN_PY" "$build_dir"
    "$uv" pip install --python "$build_dir/bin/python" "$spec"
  else
    python=$(find_python) || die "no Python >= $MIN_PY found (no uv either) — brew install uv (or python@3.12), then re-run"
    say "using $python"
    "$python" -m venv "$build_dir"
    "$build_dir/bin/python" -m pip install --disable-pip-version-check "$spec"
  fi

  step "Make the install root-owned and not writable"
  chown -R root:wheel "$build_dir"
  chmod -R go-w "$build_dir"
  [[ -x $build_dir/bin/ntfsmnt ]] || die "$build_dir/bin/ntfsmnt was not installed by $spec"
  require_trusted_ancestors

  step "Swap $build_dir into $VENV_DIR"
  rm -rf "$VENV_DIR"
  mv "$build_dir" "$VENV_DIR"
  trap cleanup EXIT
  [[ -x $CLI_BIN ]] || die "$CLI_BIN is missing after the swap"
  ok "$CLI_BIN installed"

  write_sudoers
  say ""
  say "done: the app can now run 'sudo -n $CLI_BIN web' without a password."
}

run_uninstall() {
  step "Remove $SUDOERS_FILE (the grant goes first, the binary second)"
  rm -f "$SUDOERS_FILE"
  if [[ -e $SUDOERS_FILE ]]; then
    die "$SUDOERS_FILE still exists"
  fi
  ok "$SUDOERS_FILE is gone"

  step "Remove $VENV_DIR"
  if [[ -d $VENV_DIR ]]; then
    rm -rf "$VENV_DIR"
    [[ ! -e $VENV_DIR ]] || die "$VENV_DIR still exists"
    ok "$VENV_DIR removed"
  else
    ok "$VENV_DIR was already absent"
  fi
  say ""
  say "uninstalled."
}

run_check() {
  local uname_s uv py owner mode writable bad

  uname_s=$(uname -s)
  if [[ $uname_s == Darwin ]]; then
    ok "macOS (Darwin $(uname -r))"
  else
    fail "macOS is required (uname -s = $uname_s)"
  fi

  if [[ $EUID -eq 0 ]]; then
    info "running as root; install and uninstall can write $VENV_DIR"
  else
    info "not root (EUID $EUID); install and uninstall need sudo, this check stays read-only"
  fi

  if [[ $uname_s == Darwin ]]; then
    bad=$(untrusted_ancestors)
    if [[ -z $bad ]]; then
      ok "install path is root-owned and not group/other-writable"
    else
      fail "install path is writable by an admin user:$bad"
    fi
  fi

  if uv=$(find_uv); then
    ok "uv at $uv ($("$uv" --version 2>/dev/null || say 'version unknown'))"
  elif py=$(find_python); then
    ok "$py ($("$py" -V 2>&1))"
  else
    fail "no uv and no python3.12+ found — brew install uv (or python@3.12)"
  fi

  if [[ -d $VENV_DIR ]]; then
    ok "$VENV_DIR exists"
    owner=$(stat -f '%Su:%Sg' "$VENV_DIR" 2>/dev/null || true)
    if [[ $owner == root:wheel ]]; then
      ok "$VENV_DIR is owned by root:wheel"
    else
      fail "$VENV_DIR is owned by ${owner:-unknown}, expected root:wheel"
    fi
    writable=$(writable_paths)
    if [[ -z $writable ]]; then
      ok "no group/other-writable path under $VENV_DIR"
    else
      fail "group/other-writable path under $VENV_DIR: $writable"
    fi
  else
    fail "$VENV_DIR is missing — run: sudo $RUN_HINT --version <x.y.z>"
  fi

  if [[ -f $SUDOERS_FILE ]]; then
    mode=$(stat -f '%Lp' "$SUDOERS_FILE" 2>/dev/null || true)
    if [[ $mode == 440 ]]; then
      ok "$SUDOERS_FILE mode 0440"
    else
      fail "$SUDOERS_FILE mode is ${mode:-unknown}, expected 0440"
    fi
    if grep -qxF -- "$RULE" "$SUDOERS_FILE" 2>/dev/null; then
      ok "sudoers rule matches the contract (web only)"
    else
      fail "sudoers rule does not match the contract"
    fi
  else
    fail "$SUDOERS_FILE is missing — run: sudo $RUN_HINT --version <x.y.z>"
  fi

  if [[ $EUID -eq 0 ]]; then
    info "skipped 'sudo -n -l' because this shell is already root"
  elif sudo -n -l "$CLI_BIN" web --port 0 --owner "$(id -u):$(id -g)" >/dev/null 2>&1; then
    ok "sudo -n -l $CLI_BIN web --port 0 --owner <uid>:<gid> succeeds"
  else
    fail "sudo -n -l $CLI_BIN web ... fails — the app cannot start the dashboard"
  fi

  printf '\nsummary: %d ok, %d FAIL, %d info\n' "$OK" "$FAILED" "$INFO"
  if ((FAILED > 0)); then
    say "next: sudo $RUN_HINT --version <x.y.z>"
  fi
  return 0
}

MODE=install
VERSION=''
SDIST=''
for arg in "$@"; do
  ORIGINAL_ARGS_STR+=" $(printf '%q' "$arg")"
done

while (($#)); do
  case $1 in
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    --version)
      (($# >= 2)) || { usage >&2; exit 2; }
      VERSION=$2
      shift
      ;;
    --sdist)
      (($# >= 2)) || { usage >&2; exit 2; }
      SDIST=$2
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

if [[ $MODE == check ]]; then
  run_check
  exit 0
fi

require_root
require_macos

if [[ $MODE == uninstall ]]; then
  run_uninstall
  exit 0
fi

install_cli
