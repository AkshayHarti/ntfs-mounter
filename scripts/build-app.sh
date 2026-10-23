#!/usr/bin/env bash
# Build the unsigned ntfsmnt menu-bar app into dist/ntfsmnt.app.
# Safe to re-run: the bundle is deleted and reassembled every time.
set -euo pipefail

if [ "$(uname -s)" != "Darwin" ]; then
  echo "build-app.sh: macOS only" >&2
  exit 1
fi
if ! command -v swiftc >/dev/null 2>&1; then
  echo "build-app.sh: swiftc not found (install Xcode Command Line Tools)" >&2
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
bundle="$root/dist/ntfsmnt.app"
resources="$bundle/Contents/Resources"
installer="$root/scripts/install-privileged.sh"

if [ ! -f "$installer" ]; then
  echo "build-app.sh: missing $installer (the app's Set up… button needs it)" >&2
  exit 1
fi

version="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$root/ntfsmnt/__init__.py")"
if [ -z "$version" ]; then
  echo "build-app.sh: could not read __version__ from ntfsmnt/__init__.py" >&2
  exit 1
fi

rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS" "$resources"

cp "$root/app/Info.plist" "$bundle/Contents/Info.plist"
# The bundle version must match the Python package; a stale plist is a lie the
# Finder and About box both repeat.
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $version" \
  "$bundle/Contents/Info.plist"
plutil -lint "$bundle/Contents/Info.plist"

swiftc -O -framework Cocoa "$root/app/MenuBar.swift" \
  -o "$bundle/Contents/MacOS/ntfsmnt-menu"

install -m 0755 "$installer" "$resources/install-privileged.sh"
cmp -s "$installer" "$resources/install-privileged.sh" || {
  echo "build-app.sh: the bundled installer differs from $installer" >&2
  exit 1
}

# The .command runs in Terminal (one password prompt). ${0:A:h} is zsh's own
# path resolution, so it works no matter where the bundle lives.
{
  printf '%s\n' '#!/bin/zsh'
  printf 'exec sudo /bin/bash "${0:A:h}/install-privileged.sh" --version %s\n' "$version"
} > "$resources/install-privileged.command"
chmod 0755 "$resources/install-privileged.command"
zsh -n "$resources/install-privileged.command"

codesign --force --deep --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
codesign -dv "$bundle" 2>&1
du -h "$bundle"
