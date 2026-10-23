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

rm -rf "$bundle"
mkdir -p "$bundle/Contents/MacOS"

cp "$root/app/Info.plist" "$bundle/Contents/Info.plist"
plutil -lint "$bundle/Contents/Info.plist"

swiftc -O -framework Cocoa "$root/app/MenuBar.swift" \
  -o "$bundle/Contents/MacOS/ntfsmnt-menu"

codesign --force --deep --sign - "$bundle"
codesign --verify --deep --strict "$bundle"
codesign -dv "$bundle" 2>&1
du -h "$bundle"
