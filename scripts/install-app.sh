#!/usr/bin/env bash
# Build and install ntfsmnt.app.
#
#   scripts/install-app.sh [--user] [--launch] [--dry-run]
#
#   --user     install to ~/Applications instead of /Applications
#   --launch   open the installed bundle when the copy finishes
#   --dry-run  print the plan and exit without touching anything
#
# sudo is used only for the copy into /Applications.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
target="/Applications/ntfsmnt.app"
with_sudo=1
launch=0
dry_run=0

usage() {
  cat <<'EOF'
usage: scripts/install-app.sh [--user] [--launch] [--dry-run]

  --user     install to ~/Applications/ntfsmnt.app (no sudo)
  --launch   open the installed app
  --dry-run  print the plan and exit 0
EOF
}

for arg in "$@"; do
  case "$arg" in
    --user)
      with_sudo=0
      target="${HOME:?HOME is not set}/Applications/ntfsmnt.app"
      ;;
    --launch) launch=1 ;;
    --dry-run) dry_run=1 ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "install-app.sh: unknown option: $arg" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ "$dry_run" -eq 1 ]; then
  echo "ntfsmnt app install plan (dry run)"
  echo "  build   bash $root/scripts/build-app.sh"
  echo "  source  $root/dist/ntfsmnt.app"
  if [ "$with_sudo" -eq 1 ]; then
    echo "  copy    $target  (sudo is used only for this copy)"
  else
    echo "  copy    $target"
  fi
  echo "  xattr   xattr -dr com.apple.quarantine \"$target\""
  if [ "$launch" -eq 1 ]; then
    echo "  launch  open \"$target\""
  fi
  echo "Nothing was changed."
  exit 0
fi

if [ "$(uname -s)" != "Darwin" ]; then
  echo "install-app.sh: macOS only" >&2
  exit 1
fi

bash "$root/scripts/build-app.sh"

source_bundle="$root/dist/ntfsmnt.app"
if [ ! -d "$source_bundle" ]; then
  echo "install-app.sh: build did not produce $source_bundle" >&2
  exit 1
fi

parent="$(dirname "$target")"

if [ "$with_sudo" -eq 1 ]; then
  echo "Copying to $target (sudo is used only for this copy)"
  sudo mkdir -p "$parent"
  # Replace in place: remove the destination bundle only, never a glob.
  sudo rm -rf "$target"
  sudo ditto "$source_bundle" "$target"
else
  mkdir -p "$parent"
  rm -rf "$target"
  ditto "$source_bundle" "$target"
fi

# A downloaded bundle carries the quarantine bit; this build is ad-hoc signed
# and runs locally.
xattr -dr com.apple.quarantine "$target" 2>/dev/null || true

echo "Installed $target"

if [ "$launch" -eq 1 ]; then
  open "$target"
fi
