# The menu-bar app

`ntfsmnt.app` is a small menu-bar controller for the local dashboard. It is
written in Swift against Cocoa only (`app/MenuBar.swift`, built with `swiftc`;
there is no Swift Package and no dependency). It is an `LSUIElement` app: no
window, no Dock icon.

## What it does

It starts and stops the loopback web dashboard for you, without a Terminal
window and without a password after the one-time setup. The menu depends on
the app's state:

| State | Menu |
| --- | --- |
| Setup needed | **Set up…**, **Setup guide**, **Grant Full Disk Access…**, **Quit** |
| Stopped | **Start dashboard**, **Setup guide**, **Grant Full Disk Access…**, **Quit** |
| Starting | **Starting…** (disabled), **Quit** |
| Running | **Open dashboard**, **Stop dashboard**, **Setup guide**, **Grant Full Disk Access…**, **Quit** |

- **Set up…** opens the bundled `Contents/Resources/install-privileged.command`
  in Terminal. Terminal runs `scripts/install-privileged.sh`, which asks for
  your admin password once, installs a root-owned copy of the CLI at
  `/usr/local/libexec/ntfsmnt/bin/ntfsmnt`, and writes the narrow sudoers rule.
- **Start dashboard** runs exactly:

  ```
  /usr/bin/sudo -n /usr/local/libexec/ntfsmnt/bin/ntfsmnt web --port 0 --owner <uid>:<gid>
  ```

  It reads the child's stdout for the line

  ```
  ntfsmnt web: http://127.0.0.1:<port>/?token=<token>
  ```

  and opens that URL with `NSWorkspace`. The token goes from the pipe to the
  browser and is never written to disk. `--port 0` asks the OS for a free port;
  `--owner` keeps mount-point ownership with the logged-in user.
- **Open dashboard** re-opens the URL the server printed, in case the tab was
  closed. The token in that URL is still the live one.
- **Stop dashboard** sends `SIGTERM` to the server process the app started. If
  it is still alive five seconds later, the app sends `SIGKILL` to that same
  child process only. This is the HTTP server, never a disk, mount, unmount, or
  repair operation.
- **Quit** stops the server first, so the app never leaves an orphaned root
  server behind.
- **Grant Full Disk Access…** explains the step and then opens System Settings →
  Privacy & Security → Full Disk Access with the app selected in Finder. macOS
  does **not** list ntfsmnt until you add it: click **+** and choose
  `/Applications/ntfsmnt.app`, or drag the app into the list. Relaunch the app
  afterwards.
- **Setup guide** opens <https://akshayharti.github.io/ntfs-mounter/>.

Detection uses the contract check,
`sudo -n -l <root-cli> web --port 0 --owner <uid>:<gid>` — the full argv, because
the rule carries arguments and sudo only matches an invocation that matches
them. It runs when the app launches and whenever the menu opens, so completing
Set up… is picked up without relaunching.

If the server exits before it prints a URL, the app shows an alert with the
captured stderr (truncated to about 2000 characters) and returns to the stopped
state. Exit code 1 with `a password is required` means the rule is missing; the
app shows the setup alert instead.

## What it deliberately does not do

- **No privileged helper.** There is no `SMJobBless` helper, no setuid binary,
  no `AuthorizationExecuteWithPrivileges`, and no `osascript`. The only
  privilege path is `sudo -n` against the rule installed by
  `install-privileged.sh`.
- **No always-on server.** The server starts when you ask and dies with the
  app. There is no LaunchAgent or LaunchDaemon, and nothing listens before you
  press Start.
- **No volume access.** The app never mounts, unmounts, probes, or reads a
  mounted volume, and it does not install FUSE-T or `ntfs-3g`. All of that is
  the CLI's job.
- **No shell.** `Process` is given an argv array, never a command string, so
  paths with spaces work and nothing is interpolated.
- **Not notarized.** The bundle is ad-hoc signed. See [Signing and
  notarizing](#signing-and-notarizing).

## First run

1. Build and install the app:

   ```bash
   bash scripts/install-app.sh             # /Applications, uses sudo for the copy
   bash scripts/install-app.sh --user      # ~/Applications, no sudo
   ```

   Add `--launch` to open it when the copy finishes. `--dry-run` prints the
   plan and changes nothing.

2. Click the menu-bar item and choose **Set up…**. Terminal opens, asks for
   your admin password once, and runs `install-privileged.sh`. When it
   finishes, reopen the menu: it should now offer **Start dashboard**.

3. Grant Full Disk Access to the **app**. The root server inherits the app's
   TCC attribution, so without this the CLI cannot read the raw disk and
   mounts fail with `EPERM`. Use **Grant Full Disk Access…**; the app is not in
   the list until you add it, so click **+** and choose
   `/Applications/ntfsmnt.app` (or drag it in from the Finder window), then turn
   the switch on. Relaunch the app after granting it.

4. Choose **Start dashboard**. The browser opens on the token URL and the menu
   changes to **Open dashboard** / **Stop dashboard**.

## The standing grant, in plain words

Setup allows members of the `admin` group to run **only** the `web` subcommand
of a root-owned copy of the CLI, without a password:

```
%admin ALL=(root) NOPASSWD: /usr/local/libexec/ntfsmnt/bin/ntfsmnt web *
```

That is a real tradeoff. Any admin user on this Mac can start a root web server
on loopback without being asked for a password. There is no shell, no arbitrary
command, and no mount or unmount behind that grant. The server still enforces
the same web boundary it always did: a one-time launch token exchanged for a
session cookie, `X-NTFSMNT: 1` plus an allowed `Origin` on every mutation, a
loopback-only `Host` allow-list, and a single operation lock per server
process. It binds `127.0.0.1` on a random port and exits when you stop it or
quit the app.

The lock serializes requests inside one server; it is not a host-wide lock. Two
servers started by two copies of the app are independent, which is why the app
blocks Start while a stop is still winding down.

A crash (as opposed to **Quit**) can leave the server it started running. Find
it with
`pgrep -f '/usr/local/libexec/ntfsmnt/bin/ntfsmnt web'` and stop it with
`sudo kill <pid>`; a normal Quit waits 5 s and then forces only its own server
tree.

The grant is opt-in: it exists only after you run **Set up…**, and
`install-privileged.sh --uninstall` removes it. The binary it points at is
root-owned and not group/other-writable, because a `NOPASSWD` rule aimed at a
user-writable path would be passwordless root for anyone who can edit that path.
The installer checks every parent directory too and refuses when one is not
root-owned or is group/other-writable — as `/usr/local` can be on Intel
Homebrew machines.

## Terminal use still works

The app does not replace the CLI. `sudo ntfsmnt web --open` in Terminal is
still the same dashboard with the same token boundary. When you use Terminal,
privilege and TCC attribution stay with Terminal, so Terminal needs Full Disk
Access — the app's grant does not transfer to it, and vice versa. Pick whichever
you prefer; they do not conflict, and only one dashboard can hold the operation
lock at a time.

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
dist/ntfsmnt.app/Contents/Resources/install-privileged.sh
dist/ntfsmnt.app/Contents/Resources/install-privileged.command
```

It lints the plist (`plutil -lint`), compiles with
`swiftc -O -framework Cocoa`, copies the installer into `Contents/Resources`,
generates the `.command` wrapper from the version in `ntfsmnt/__init__.py`,
checks it with `zsh -n`, ad-hoc signs (`codesign --force --deep --sign -`), and
then prints `codesign --verify --deep --strict`, `codesign -dv`, and the bundle
size. `dist/` is git-ignored.

## Uninstall

Remove the grant before the binary, in that order:

```bash
sudo ./scripts/install-privileged.sh --uninstall
```

Then delete the app (drag it to the Trash, or):

```bash
sudo rm -rf /Applications/ntfsmnt.app   # or ~/Applications with --user
```

Your volumes and mounts are untouched: the app never owned them.

## Running it unsigned

The bundle is ad-hoc signed, which is enough for Gatekeeper on the same Mac but
not a substitute for notarization on someone else's. A bundle you built locally
carries no quarantine attribute, so it just opens. `install-app.sh` also clears
the quarantine bit on the destination. If macOS still refuses a downloaded
copy, either:

- Control-click (right-click) the app → **Open** → **Open** again, or
- remove the attribute:

  ```bash
  xattr -dr com.apple.quarantine /Applications/ntfsmnt.app
  ```

## Signing and notarizing

**This section is not verified without a Developer ID.** The commands below are
the real ones; they have not been run here because no Developer ID certificate
is installed on this machine. The build and ad-hoc signing steps *are* verified.

Prerequisites:

1. An Apple Developer Program membership and a **Developer ID Application**
   certificate in your login keychain. Find its name:

   ```bash
   security find-identity -v -p codesigning
   ```

   It looks like `Developer ID Application: Your Name (TEAMID)`.
2. Stored notarytool credentials in the keychain profile `AC_PASSWORD` (an
   app-specific password from appleid.apple.com, or an App Store Connect API
   key):

   ```bash
   xcrun notarytool store-credentials "AC_PASSWORD" \
     --apple-id "you@example.com" --team-id "TEAMID" --password "app-specific-password"
   ```

Then sign and notarize with `scripts/sign-app.sh`, passing the identity:

```bash
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
| No menu-bar item after launching | The app is `LSUIElement`, so there is no window and no Dock icon. Look for the drive icon in the menu bar; quit and relaunch if a menu-bar manager hid it. |
| Menu offers **Set up…** after setup | Detection runs `sudo -n -l <root-cli> web --port 0 --owner <uid>:<gid>`. Re-run **Set up…**, then check `sudo ./scripts/install-privileged.sh --check`. |
| Start shows an alert instead of the browser | Read the captured stderr in the alert. `a password is required` means the rule is missing; other messages usually mean the `web` extra or FUSE-T is missing. |
| Browser opens but mounts fail with `EPERM` | The app does not have Full Disk Access. Use **Grant Full Disk Access…**, add the app with **+**, and relaunch it. |
| Full Disk Access does not list ntfsmnt | macOS never adds an app by itself. Click **+** and choose `/Applications/ntfsmnt.app`, or drag it in from Finder. After rebuilding the app, its ad-hoc signature changes, so add it again. |
| **Start dashboard** does nothing | The menu is probably in the stopped state but the rule was removed. Re-run setup, or check `--check` output. |

## What is verified

- `bash scripts/build-app.sh` builds, lints, ad-hoc signs, and verifies the
  bundle; the installer and generated `.command` are present in
  `Contents/Resources`, and `zsh -n` accepts the `.command`.
- `plutil -lint` and `codesign --verify --deep --strict` pass.
- `bash scripts/install-app.sh --dry-run` prints the plan and exits 0.
- No file under `app/` or `scripts/` contains `osascript`.

Not verified here: the live **Start dashboard** path (it needs the sudoers rule
from `install-privileged.sh` and a real password prompt), Full Disk Access
attribution, the menu items end to end (they open Terminal or System Settings),
and notarization/stapling (no Developer ID).
