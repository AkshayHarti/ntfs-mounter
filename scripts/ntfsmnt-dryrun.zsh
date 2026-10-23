#!/usr/bin/env zsh
# ntfsmnt dry-run helper
# Safe: only echoes commands + runs read-only checks. Never calls mount/unmount/eject as root.
set -e

REPO="$HOME/workspace/repos/ntfs-mounter"
cd "$REPO"

echo "== ntfsmnt DRY RUN =="
echo "Repo: $REPO"
echo "Date: $(date)"
echo

echo "[CHECK] macOS:"
sw_vers | head -2
echo

echo "[CHECK] Tools:"
command -v uv && uv --version || echo "MISSING: uv (https://docs.astral.sh/uv/)"
command -v diskutil && echo "ok: diskutil"
echo

echo "[CHECK] FUSE-T:"
[ -f /usr/local/lib/libfuse-t.dylib ] && echo "ok: /usr/local/lib/libfuse-t.dylib" || echo "MISSING: brew install --cask fuse-t"
ls -la /usr/local/lib/libfuse-t* 2>/dev/null | head -2
echo

echo "[CHECK] ntfs-3g:"
[ -x /usr/local/bin/ntfs-3g ] && echo "ok: /usr/local/bin/ntfs-3g" || echo "MISSING: build from macos-fuse-t/ntfs-3g"
otool -L /usr/local/bin/ntfs-3g 2>/dev/null | grep libfuse-t && echo "ok: linked against libfuse-t" || echo "WARN: not linked to libfuse-t (check above)"
echo

echo "[DRY] Install deps (read-only):"
echo "  uv sync"
echo "  uv sync --extra web  # optional for UI"
echo

echo "[DRY] Install CLI to PATH (editable):"
echo "  uv tool install --editable ."
echo

echo "[DRY] Verify environment:"
echo "  ntfsmnt doctor        # unprivileged: root/device-access expected to FAIL"
echo "  sudo ./.venv/bin/ntfsmnt doctor   # with FDA terminal: should be OK"
echo "  ntfsmnt list          # show NTFS volumes"
echo

echo "[DRY] Usage:"
echo "  ntfsmnt mount <LABEL>   # re-execs via sudo, makes writable"
echo "  printf hi > /Volumes/<LABEL>/test && rm /Volumes/<LABEL>/test"
echo "  ntfsmnt unmount <LABEL> # restores Apple's read-only mount"
echo "  ntfsmnt eject <LABEL>   # eject parent disk (must be unmounted)"
echo

echo "[DRY] Web UI:"
echo "  uv sync --extra web"
echo "  sudo ./.venv/bin/ntfsmnt web --open"
echo

echo "[INFO] This script does NOT run mount/unmount/eject or sudo actions."
echo "== END DRY RUN =="
