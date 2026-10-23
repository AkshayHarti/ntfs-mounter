# The menu-bar app

`dist/ntfsmnt.app` is a small launcher that puts `ntfsmnt` in the menu bar. It
is written in Swift against Cocoa only (`app/MenuBar.swift`, built with
`swiftc`; there is no Swift Package and no dependency).

## What it does

The menu has four items:

| Item | What happens |
| --- | --- |
| Open dashboard | Writes `~/Library/Application Support/ntfsmnt/open-dashboard.command` (mode 0700) containing `#!/bin/zsh` and `exec sudo <ntfsmnt> web --open`, then opens it. Terminal runs `sudo ntfsmnt web --open` and prompts for your password. |
| Run doctor | Same pattern with `run-doctor.command`: `<ntfsmnt> doctor; echo; read -r "Okay (press Return)"`. The window stays open until you press Return. |
| Setup guide | Opens <https://akshayharti.github.io/ntfs-mounter/> in your browser. |
| Quit | Terminates the app. |

`<ntfsmnt>` is the first executable found in this order: each directory in the
app's `PATH`, then `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`. If
none has `ntfsmnt`, the first two items show an error alert with three
copyable install commands and an **Open setup guide** button.

The app is an `LSUIElement` (menu-bar-only) app: no window, no Dock icon.

## What it deliberately does not do

- **No embedded server.** The dashboard is `sudo ntfsmnt web --open` in
  Terminal, exactly the command from the README. The app does not start,
  proxy, or supervise it, and it does not know the launch token.
- **No privileged helper.** There is no `SMJobBless` helper, no setuid binary,
  no `AuthorizationExecuteWithPrivileges`, and no `osascript`. The only
  privilege path is `sudo` prompting in your terminal — the same path the CLI
  uses, so the terminal's Full Disk Access attribution is preserved.
- **No volume access.** The app never mounts, unmounts, probes, or reads a
  mounted volume, and it does not install FUSE-T or `ntfs-3g`. All of that is
  the CLI's job.
- **No Full Disk Access for the app.** The app itself does not need it. The
  *terminal* that runs `sudo ntfsmnt ...` still needs Full Disk Access; see
  [TCC / Full Disk Access](../README.md#tcc--full-disk-access) in the README.
  Granting it to the app would not help, because the app never opens a device.
- **No icon.** `CFBundleIconName` is omitted; there is no icon asset yet, so
  the generic app icon is used.

## Build

From the repo root:

```bash
bash scripts/build-app.sh
```

The script requires macOS and `swiftc` (Xcode Command Line Tools). It deletes
and rebuilds `dist/ntfsmnt.app` on every run:

```
dist/ntfsmnt.app/Contents/Info.plist
dist/ntfsmnt.app/Contents/MacOS/ntfsmnt-menu
```

It lints the plist (`plutil -lint`), compiles with
`swiftc -O -framework Cocoa`, ad-hoc signs (`codesign --force --deep --sign -`),
then prints `codesign --verify --deep --strict`, `codesign -dv`, and the bundle
size. `dist/` is git-ignored; nothing is installed into `/Applications`.

To use it, move or copy the bundle wherever you like (for example
`/Applications`) and launch it. The app resolves `ntfsmnt` at click time, so
upgrading the CLI needs no rebuild.

## Running it unsigned

The bundle is ad-hoc signed, which is enough for Gatekeeper on the same Mac but
not a substitute for notarization on someone else's. A bundle you built locally
carries no quarantine attribute, so it just opens. If you copied a downloaded
copy that macOS quarantined, either:

- Control-click (right-click) the app → **Open** → **Open** again, or
- remove the attribute:

  ```bash
  xattr -dr com.apple.quarantine /Applications/ntfsmnt.app
  ```

Ad-hoc signed builds are not notarized; macOS may still refuse them on a
machine that has never seen them.

## Signing and notarizing

**This section is verified only when you have a Developer ID.** The commands
below are the real ones; they were not run here because no Developer ID
certificate is installed on this machine. The build and ad-hoc signing steps
*are* verified.

Prerequisites:

1. An Apple Developer Program membership and a **Developer ID Application**
   certificate in your login keychain. Find its name:

   ```bash
   security find-identity -v -p codesigning
   ```

   It looks like `Developer ID Application: Your Name (TEAMID)`.
2. Stored notarytool credentials in the keychain profile `AC_PASSWORD` (an
   app-specific password from appleid.apple.com, not your Apple ID password):

   ```bash
   xcrun notarytool store-credentials "AC_PASSWORD" \
     --apple-id "you@example.com" \
     --team-id "TEAMID" \
     --password "app-specific-password"
   ```

Then build, sign, notarize, and staple:

```bash
bash scripts/build-app.sh
bash scripts/sign-app.sh "Developer ID Application: Your Name (TEAMID)"
```

`sign-app.sh` runs, in order:

```bash
codesign --force --deep --options runtime --timestamp --sign "<identity>" dist/ntfsmnt.app
codesign --verify --deep --strict dist/ntfsmnt.app
ditto -c -k --keepParent dist/ntfsmnt.app dist/ntfsmnt.zip
xcrun notarytool submit dist/ntfsmnt.zip --keychain-profile "AC_PASSWORD" --wait
xcrun stapler staple dist/ntfsmnt.app
```

The zip is created in `dist/` and nothing is written outside it. `--wait`
blocks until Apple finishes; a first submission usually takes a few minutes.
Confirm the ticket is attached:

```bash
xcrun stapler validate dist/ntfsmnt.app
spctl --assess --type execute --verbose=4 dist/ntfsmnt.app
```

`sign-app.sh --adhoc` (or no argument) exits 1 with the usage text: ad-hoc
signing cannot be notarized, and `build-app.sh` already does it for local use.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| No menu-bar item after launching | The app is `LSUIElement`, so there is no window and no Dock icon. Look for `ntfsmnt` in the menu bar; quit and relaunch if it is hidden by a menu-bar manager. |
| "ntfsmnt is not installed" alert | The CLI is not on the app's `PATH` nor in `/opt/homebrew/bin`, `/usr/local/bin`, `~/.local/bin`. Install it with one of the commands in the alert. |
| Dashboard opens and immediately fails | Terminal needs Full Disk Access, or FUSE-T/`ntfs-3g` is missing. Use **Run doctor** and follow its output. |
| Nothing happens on **Open dashboard** | Terminal is not the default handler for `.command` files, or the Terminal window opened behind other windows. The file is at `~/Library/Application Support/ntfsmnt/open-dashboard.command`; you can run it directly. |

## What is verified

- `bash scripts/build-app.sh` builds, lints, ad-hoc signs, and verifies the
  bundle on this machine (Swift 6.3.3, macOS 26).
- The built app launches, registers as `UIElement` (menu-bar only), and quits
  on SIGTERM.
- No file under `app/` contains `osascript`.

Not verified here: notarization and stapling (no Developer ID), and the menu
items end to end (they open Terminal windows and require a sudo password, so
they need a human).
