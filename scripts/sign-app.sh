#!/usr/bin/env bash
# Sign and notarize dist/ntfsmnt.app with a Developer ID. Requires an Apple
# Developer ID certificate in the keychain and a stored notarytool profile.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
usage: scripts/sign-app.sh "<Developer ID Application: Your Name (TEAMID)>"

  codesigns dist/ntfsmnt.app with a Developer ID Application certificate,
  then staples the notarization ticket to the bundle.

  An Apple Developer ID is required for notarization; there is no ad-hoc path
  here (scripts/build-app.sh already ad-hoc signs the bundle for local use).

  Notarytool credentials come from the keychain profile "AC_PASSWORD":
    xcrun notarytool store-credentials "AC_PASSWORD" \
      --apple-id <you@example.com> --team-id <TEAMID> --password <app-specific-password>

  Build first: bash scripts/build-app.sh
EOF
}

identity="${1:-}"
if [ -z "$identity" ] || [ "$identity" = "--adhoc" ]; then
  usage
  exit 1
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
bundle="$root/dist/ntfsmnt.app"
zip="$root/dist/ntfsmnt.zip"

if [ ! -d "$bundle" ]; then
  echo "sign-app.sh: $bundle not found; run bash scripts/build-app.sh first" >&2
  exit 1
fi

codesign --force --deep --options runtime --timestamp --sign "$identity" "$bundle"
codesign --verify --deep --strict "$bundle"

rm -f "$zip"
ditto -c -k --keepParent "$bundle" "$zip"

xcrun notarytool submit "$zip" --keychain-profile "AC_PASSWORD" --wait
xcrun stapler staple "$bundle"

echo "signed and notarized: $bundle"
