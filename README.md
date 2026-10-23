# ntfsmnt

Read-write NTFS on macOS, from the command line.

macOS mounts NTFS read-only through its built-in FSKit driver. `ntfsmnt` replaces
that mount for one volume at a time with an `ntfs-3g` mount served by
[FUSE-T](https://www.fuse-t.org/), so ordinary user files can be written without
a kernel extension, without disabling SIP, and without a reboot.

```
$ uv run ntfsmnt list
DEVICE        LABEL      SIZE           STATE      MOUNT
/dev/disk4s1  HD1        1000296415232  ro         /Volumes/HD1
/dev/disk4s2  HD2        1000065728512  ro         /Volumes/HD2

$ uv run ntfsmnt mount HD1
mounted HD1 at /Volumes/HD1

$ printf hello > /Volumes/HD1/hello.txt     # no sudo
$ rm /Volumes/HD1/hello.txt
$ uv run ntfsmnt unmount HD1
unmounted HD1
```

The tool reports what it actually did. It never guesses a volume, never repairs
a filesystem on its own, and puts Apple's read-only mount back when an attempt
fails.

## Requirements

Verified on Apple Silicon, macOS 26.5.2 (25F84).

| Component | Version / location | Notes |
|---|---|---|
| macOS | 26.x on Apple Silicon | Intel untested |
| Python | ≥ 3.12 | no runtime third-party dependencies |
| [uv](https://docs.astral.sh/uv/) | any recent version | for install and development |
| FUSE-T | 1.2.9, `/usr/local/lib/libfuse-t.dylib` | installed by [`scripts/setup.sh`](scripts/setup.sh) |
| ntfs-3g | 2022.10.3 (built from `edge`), `/usr/local/bin/ntfs-3g` | FUSE-T fork, must link `libfuse-t`; same script |
| Terminal | Full Disk Access granted | see [TCC / Full Disk Access](#tcc--full-disk-access) |

[`scripts/setup.sh`](scripts/setup.sh) installs both native prerequisites: the
FUSE-T cask with `brew install --cask fuse-t`, and the FUSE-T-linked `ntfs-3g`
built from <https://github.com/macos-fuse-t/ntfs-3g> (branch `edge`, overridable
with `NTFS3G_REF`) into `/usr/local/bin/ntfs-3g`. It installs the build tooling
(`autoconf`, `automake`, `libtool`, `pkgconf`) through Homebrew when missing and
prints the resolved `ntfs-3g` commit. Run `./scripts/setup.sh --check` first to
see what is missing without changing anything.

To build `ntfs-3g` by hand instead:

```bash
git clone https://github.com/macos-fuse-t/ntfs-3g.git
cd ntfs-3g
./autogen.sh && ./configure && make
sudo make install
otool -L /usr/local/bin/ntfs-3g | grep libfuse-t
```

`ntfs-3g` here is the FUSE-T fork, not the macFUSE build and not a Homebrew
build. On macOS the fork hardcodes `-I/usr/local/include/fuse` and `-lfuse-t`
in `src/Makefile.am`, so the build needs no extra flags (configure only uses
pkg-config to locate the library directory). `doctor` reports a missing
FUSE-T, an `ntfs-3g` that is not linked against FUSE-T, a missing root, and a
device that cannot be opened.

## Install

Four blocks: the native prerequisites once per Mac, the CLI from PyPI, a
Homebrew tap formula, and a checkout for contributors.

### 1. Prerequisites

From a checkout, run the setup script:

```bash
git clone https://github.com/AkshayHarti/ntfs-mounter.git
cd ntfs-mounter
./scripts/setup.sh
```

It installs FUSE-T and builds `ntfs-3g` against it, calling `sudo` only for
`make install`; the FUSE-T cask asks for an administrator password through its
own installer. It is idempotent — satisfied steps are skipped — and
`./scripts/setup.sh --check` reports the state without changing anything. It
ends with the Full Disk Access reminder; that grant is manual, see
[TCC / Full Disk Access](#tcc--full-disk-access).

### 2. The CLI from PyPI

```bash
uv tool install "ntfsmnt[web]"
ntfsmnt doctor
```

`pipx install "ntfsmnt[web]"` is the alternative if you use pipx. Drop `[web]`
for the CLI without the browser UI. `ntfsmnt doctor` reports FUSE-T, `ntfs-3g`,
root, and device access; run unprivileged, the `root` and `device-access` checks
fail by design. `mount`, `unmount`, and `eject` re-exec through `sudo` from your
terminal — see [Privileges and the sudo re-exec](#privileges-and-the-sudo-re-exec)
— which is why that terminal needs Full Disk Access.

### 3. The CLI from Homebrew

```bash
brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter
brew install AkshayHarti/ntfs-mounter/ntfsmnt
```

One honest sentence about the URL: the tap repo is named `ntfs-mounter`, not
`homebrew-ntfs-mounter`, because tap and project live in this same repo — so
plain `brew tap AkshayHarti/ntfs-mounter` would look for a `homebrew-ntfs-mounter`
repo on GitHub and `brew tap` needs the full URL. The tap installs two
formulae: `ntfsmnt` and `ntfs-3g-fuse-t`, a port of the FUSE-T-linked `ntfs-3g`
the setup script builds by hand. Details, including how they differ from
`scripts/setup.sh` and what is verified, are in
[docs/HOMEBREW.md](docs/HOMEBREW.md). On Homebrew 7+, third-party taps must be
trusted before their formulae load (`brew trust --tap AkshayHarti/ntfs-mounter`
after `brew tap` errors with a trust warning).

The tap formula refuses to install while the macFUSE-linked
`gromgit/fuse/ntfs-3g-mac` is installed (both want `/opt/homebrew/bin/ntfs-3g`);
uninstall that one first.

### 4. From a checkout (development)

```bash
git clone https://github.com/AkshayHarti/ntfs-mounter.git
cd ntfs-mounter
uv sync --extra web
uv run ntfsmnt list
```

`uv run ntfsmnt ...` runs the checkout's code. `uv tool install --editable .`
puts that same checkout on your `PATH`; the installed command re-execs through
`sudo` using the interpreter of the environment it was installed into, so it
keeps working outside the checkout.

## Commands

```
ntfsmnt list [--json]
ntfsmnt doctor [--json]
ntfsmnt status <volume> [--json]
ntfsmnt mount <volume> [--recover] [--remove-hiberfile] [--json]
ntfsmnt unmount <volume> [--keep-unmounted] [--json]
ntfsmnt eject <volume> [--json]
ntfsmnt web [--port N] [--open] [--owner UID:GID]
```

A `<volume>` can be a label (`HD1`), a device node (`/dev/disk4s1`), a diskutil
identifier (`disk4s1`), a volume UUID, or a mount point (`/Volumes/HD1`).
Identifiers, UUIDs, labels, and mount points are tried in that order. If a
label matches more than one attached volume, the command fails and lists the
candidates rather than picking one.

### `list`

```bash
uv run ntfsmnt list
```

Prints one row per NTFS volume currently known to `diskutil`:

- `DEVICE` — device node.
- `LABEL` — volume name.
- `SIZE` — size in bytes.
- `STATE` — `rw` when a `fuse-t:` mount serves the volume, `ro` when Apple's
  read-only mount is present, `unmounted` otherwise.
- `MOUNT` — mount point, or `-`.

Volumes served by FUSE-T are NFS mounts: `diskutil list` still lists the
partition, but it does not report our NFS mount as a mount point. `ntfsmnt list`
combines `diskutil` device information with the live mount table.

### `doctor`

```bash
uv run ntfsmnt doctor
```

Checks, in order: macOS/architecture (informational), FUSE-T library present,
`ntfs-3g` present and linked against FUSE-T, running as root, and whether the
first NTFS device can be opened read-write with `ntfs-3g.probe`. The licence
line is informational.

Checks that know a remedy print a `fix:` line. Exit code is `1` when any hard
check (`fuse-t`, `ntfs-3g`, `root`, `device-access`) fails, `0` otherwise.

Run it unprivileged to see the environment, and with `sudo` from the same
terminal to check root and device access:

```bash
uv run ntfsmnt doctor          # root and device-access FAIL is expected
sudo uv run ntfsmnt doctor     # every check should be ok
```

If `device-access` fails as root, read the detail before assuming TCC. A `busy`
result means the probed volume is still mounted: unmount it, re-run `doctor`,
then remount. A `Permission denied` / `EPERM` result is the Full Disk Access
signal.

If `sudo` cannot find `uv`, use `sudo "$(command -v uv)" run ntfsmnt doctor`.

### `status`

```bash
uv run ntfsmnt status HD1
# HD1 rw fuse-t /Volumes/HD1 /dev/disk4s1 48213
```

Prints label, effective state, driver, mount point, device, and the serving
process id.

- `driver` — `fuse-t` when `ntfsmnt` serves the volume read-write, `fskit` when
  Apple's read-only mount is the one present, `-` when the volume is unmounted.
- `pid` — the `ntfs-3g` process serving a read-write mount, found with
  `pgrep -f 'ntfs-3g .*<device>( |$)'`; `-` when there is none.

### `mount`

```bash
uv run ntfsmnt mount HD1
```

This is the command that makes a volume writable. It:

1. resolves the volume and refuses if the label is ambiguous;
2. refuses a volume with no label — the label names the mount — before it
   touches anyone's mount (see [Limitations](#limitations));
3. returns success immediately when the volume already has a FUSE-T mount **at
   that mount point**: with a unique label any `fuse-t:/<label>` entry is this
   volume's, even if `pgrep` cannot answer; with a shared label only a mount
   whose serving `ntfs-3g` process names *this* device counts. A live mount of
   this label somewhere else is not a success for this path — `mount` targets
   the requested mount point;
4. creates the mount point if needed;
5. unmounts Apple's read-only mount of this device, or Apple's mount at that
   mount point. It **never** unmounts a FUSE-T mount;
6. waits for the device node to reappear (10 one-second attempts);
7. runs `ntfs-3g <device> <mount point>` with
   `uid=<you>,gid=<your group>,umask=022,local,auto_xattr,noatime,volname=<label>`;
8. waits up to 30 seconds for the `fuse-t:` NFS mount to appear, and evicts a
   competing Apple mount if it reappeared at a different mount point;
9. writes, reads back, and deletes `.ntfsmnt-probe` inside the volume;
10. checks with `stat` that the mount point is owned by *your* uid and gid —
    the probe ran as root, so it cannot prove that you can write;
11. prints `mounted HD1 at /Volumes/HD1`.

If `/Volumes/DATA` is already a read-write mount of another volume that shares
this volume's label, `mount` refuses and names the path and the label instead of
evicting the live mount:

```
ntfsmnt: mount point /Volumes/DATA is already a read-write mount; its volume
shares the label 'DATA' with /dev/disk6s1 — unmount it before mounting this one
```

Unmount it first (by its mount point, or by the other volume's device node) and
retry.

If any step fails, Apple's read-only mount is restored before the command exits
`1` with the reason. A mount that exists but cannot be written is a failure, not
a success. When `ntfs-3g` exits cleanly but the mount never appears, the message
says so and names the timeout instead of reporting an `exit code 0` failure.

Options:

- `--recover` — let `ntfs-3g` replay the NTFS journal on a dirty volume. This
  mutates the filesystem. See [Hibernated and dirty volumes](#hibernated-and-dirty-volumes).
- `--remove-hiberfile` — delete `hiberfil.sys` and mount a volume left
  hibernated by Windows Fast Startup. This discards the hibernated Windows
  session and **requires you to type the volume label** before anything runs.
  See the same section.
- `--json` — see [JSON output](#json-output).

### `unmount`

```bash
uv run ntfsmnt unmount HD1
```

Tears down the FUSE-T mount, then unmounts Apple's read-only mount for the same
device, then restores Apple's read-only mount. The result is the state you
started in: `/Volumes/HD1` present and `ro`.

It tries `diskutil unmount`, then `umount`. If the volume is busy it sends
`SIGTERM` to the serving `ntfs-3g` process once and retries. If it is still
busy, it reports which mount point is in use and exits `1`; it never forces or
kills with `SIGKILL`.

Which mounts belong to the volume is decided by the entry's own source first, then
by the serving process. A `fuse-t:/<label>` entry can only belong to a volume
with that label; an entry naming another label is another volume's mount and is
left alone, whatever process is running. Within the volume's label: when exactly
one NTFS volume carries the label, the entry is that volume's and `pgrep` is only
corroboration; when two or more share it, only the serving `ntfs-3g` process can
decide. If the label is shared and `pgrep` cannot answer, `unmount` refuses with
a manual instruction rather than guessing, and it never unmounts a `fuse-t:`
entry that resolves to another volume:

```
ntfsmnt: The mount at /Volumes/HD1 appears to belong to another volume with the
same label 'HD1'; it was not unmounted. If it should be gone, unmount it
manually: sudo diskutil unmount /Volumes/HD1
```

A live mount that names a label the volume no longer carries is the relabelled
case. It sits at the volume's own path, so it is not reported as unmounted:

```
ntfsmnt: A read-write mount at /Volumes/HD1-old serves this volume but its label
differs from 'HD1': the entry carries 'HD1-old'. Unmount it manually with:
sudo diskutil unmount /Volumes/HD1-old
```

`eject` counts that entry as mounted too: it refuses with "Unmount it first"
instead of ejecting a disk that still has a live ntfs-3g mount on it.

If a busy volume is still mounted after `SIGTERM`, the read-only restore is
skipped rather than forced through a live mount — mounting then would be
refused or land at `/Volumes/HD1 1`. The command reports the restore as failed
and leaves the read-write mount in place.

If the read-only mount cannot be put back, the unmount still happened, so the
command says so and exits `1`:

```
ntfsmnt: unmounted HD1 (read-only restore failed; run sudo diskutil mount /dev/disk4s1)
```

`--keep-unmounted` stops after the unmount and does not restore the read-only
mount. Use it before `eject`.

Running `unmount` on a volume that is already read-only is safe: it unmounts
and remounts Apple's read-only mount.

### `eject`

```bash
uv run ntfsmnt unmount HD1 --keep-unmounted
uv run ntfsmnt eject HD1
```

Ejects the **parent disk** (for `/dev/disk4s1` that is `disk4`), so every volume
on the same physical drive disappears with it. The guard is per-volume: `eject`
refuses only when the named volume itself is still mounted — checked against the
live mount table and the serving `ntfs-3g` process, so a mount served by a
same-label volume does not count, while an entry `pgrep` cannot resolve refuses
the eject — so a still-mounted sibling such as HD2 is ejected
along with it. Re-plug the drive — or, if it is still attached, run
`sudo diskutil mount /dev/disk4s1` and `sudo diskutil mount /dev/disk4s2` — to
get both read-only mounts back.

## Web UI

An optional local web UI shows the same volumes and performs the same mount,
unmount, and eject operations as the CLI. It is not installed by default; the
`web` extra adds FastAPI, uvicorn, and Jinja2:

```bash
uv sync --extra web                     # from a checkout
uv tool install 'ntfsmnt[web]'          # installed command
```

Start it explicitly as root:

```bash
sudo uv run ntfsmnt web                 # bind an OS-chosen loopback port
sudo uv run ntfsmnt web --open          # also open the browser
sudo uv run ntfsmnt web --port 8765     # bind a fixed port
sudo uv run ntfsmnt web --owner 501:20  # mount owner, when SUDO_UID/GID is absent
```

It prints one line and keeps running until `Ctrl-C`:

```
ntfsmnt web: http://127.0.0.1:<port>/?token=<random>
```

`<port>` is the OS-chosen port, or the value of `--port`.

The URL is loopback-only. The token is a one-time launch secret: opening the URL
exchanges it for an HttpOnly session cookie and redirects to `/`, so the token
leaves the address bar but remains the capability for that session. Treat the
printed URL as a password for the duration of the run; restarting the command
generates a new token.

Started **without root**, the server runs read-only: the page shows a read-only
banner, action buttons stay disabled, and the mutation API answers `403`. It
still lists volumes and their states. `--owner` defaults to `SUDO_UID`/`SUDO_GID`
from the sudo environment; a root launch with neither those variables nor
`--owner` is also read-only, because mounts would otherwise be owned by root.

Closing the browser tab does **not** unmount, eject, or otherwise change any
volume: the server keeps running until `Ctrl-C`, and the mounts it made persist
after it exits. The CLI remains the source of truth — the page calls the same
mount, unmount, and eject code, and `ntfsmnt list` is always the authoritative
view of the current state regardless of what a stale page shows.

## JSON output

Every command accepts `--json`. Output goes to stdout as a single JSON value;
diagnostics stay on stderr.

`list` — array of volume objects:

```json
[
  {
    "device": "/dev/disk4s1",
    "label": "HD1",
    "uuid": "E69303DC-BC97-42BA-B95E-98D777D099DC",
    "size": 1000296415232,
    "state": "ro",
    "mount_point": "/Volumes/HD1"
  }
]
```

`status` — one object with the same six keys plus `driver` and `pid`:

```json
{
  "device": "/dev/disk4s1",
  "label": "HD1",
  "uuid": "E69303DC-BC97-42BA-B95E-98D777D099DC",
  "size": 1000296415232,
  "state": "rw",
  "mount_point": "/Volumes/HD1",
  "driver": "fuse-t",
  "pid": 48213
}
```

`driver` is `"fuse-t"` when `ntfsmnt` serves the volume read-write, `"fskit"`
when Apple's read-only mount is present, and `"-"` when the volume is
unmounted. `pid` is the serving `ntfs-3g` process, or `null`.

`doctor` — array of checks:

```json
[
  {"name": "fuse-t", "ok": true, "detail": "FUSE-T installed at /usr/local/lib/libfuse-t.dylib", "fix": null},
  {"name": "root", "ok": false, "detail": "running as uid 501, not root", "fix": "Run via sudo from a terminal that has Full Disk Access"}
]
```

`mount` — mount point and the serving process id when it can be found:

```json
{"mount_point": "/Volumes/HD1", "pid": 48213}
```

`pid` is `null` when the volume was already mounted by FUSE-T and no new process
was started, or when `pgrep` finds nothing.

`unmount` — `{"ok": true, "restored": true}`. `restored` is `false` when the
teardown happened but Apple's read-only mount could not be put back; the exit
code is then `1`, exactly as in the text output. `eject` — `{"ok": true}`.

The `--remove-hiberfile` confirmation prompt is written to stderr, so stdout is
a single JSON value even when you are being prompted.

`state` is one of `"rw"`, `"ro"`, `"unmounted"`. `mount_point` is `null` when
the volume is unmounted. `fix` is `null` when no fix is needed.

## Privileges and the sudo re-exec

`list`, `status`, and unprivileged `doctor` run as you. Raw `/dev/disk*` access
needs root, so `mount`, `unmount`, and `eject` re-execute the CLI through sudo:

```
ntfsmnt mount HD1
  → resolve HD1 → /dev/disk4s1
  → os.execvp("sudo", ["sudo", <python>, "-m", "ntfsmnt", "mount", "/dev/disk4s1", ...])
```

Consequences worth knowing:

- You type the password in your own terminal once per command. There is no GUI
  password dialog.
- The volume is resolved and any destructive flag is confirmed **before** the
  sudo re-exec, so a typo or a refused label confirmation never costs a password
  prompt.
- The re-exec uses the same Python interpreter that is running the CLI, so it
  works with `uv run` and with `uv tool install`.
- `SUDO_UID` / `SUDO_GID` are used to set the mount's `uid`/`gid` to the user
  who invoked the command, not to root. That is why writes work without sudo
  afterwards.
- The command never uses `osascript ... with administrator privileges`. See the
  TCC note below.

## TCC / Full Disk Access

macOS attributes raw disk access to the *responsible process*, which is your
terminal application. Root alone is not enough: without Full Disk Access the
kernel returns `Operation not permitted` (`EPERM`) even for `sudo`.

Grant Full Disk Access to the terminal app you use (System Settings → Privacy &
Security → Full Disk Access), then quit and reopen that app. `doctor` reports
`device-access` failure with a `fix:` line when this is missing.

`osascript -e 'do shell script ... with administrator privileges'` is
deliberately not used. The privileged work is then attributed to `osascript`,
which cannot be granted Full Disk Access, so that path can never work. Running
`sudo` from your terminal keeps the terminal's TCC grant.

## Safety model

What the tool will not do:

- **Never** calls `ntfsfix`, `ntfsresize`, `mkntfs`, or `ntfswipe`. There is no
  auto-repair.
- **Never** passes `remove_hiberfile` unless you asked for `--remove-hiberfile`
  *and* typed the volume label. `--recover` is passed only when you ask for it.
- **Never** sends `SIGKILL` to `ntfs-3g`. The only signal it sends is `SIGTERM`,
  once, after a normal unmount failed as busy.
- The **unmount command never forces** a busy volume: it reports the mount point
  and exits `1`. At **mount time**, Apple's read-only mount is evicted with
  `diskutil unmount`, falling back to `umount -f` if that fails.
- **Never evicts a read-write FUSE-T mount.** A `fuse-t:` mount at the target
  belongs to a volume, possibly one sharing the label; `mount` refuses and names
  the path instead of unmounting it.
- **Never** guesses between volumes with the same label; it lists candidates and
  stops. `mount`, `unmount`, and `eject` refuse rather than touch a same-label
  mount they cannot attribute.
- **Never** leaves a failed mount half-applied: it restores Apple's read-only
  mount before exiting non-zero, including when the run is interrupted. The
  message says the mount was restored only when the mount table can be re-read
  and shows it; otherwise it says the restore was attempted.
- **Never** mounts a volume that has no label: relabel it first.
- **Never** mounts read-write without proving the mount is writable.
- **Never** writes to a hibernated or dirty volume automatically. Without the
  opt-in flag, the attempt fails and the read-only mount is restored.

The tool is not a filesystem checker. If NTFS is damaged, connect the volume to
Windows and let `chkdsk` handle it.

## Hibernated and dirty volumes

Two NTFS states make `ntfs-3g` refuse a read-write mount:

- **Hibernated.** Windows Fast Startup and hibernation write memory state to
  `hiberfil.sys` and leave the filesystem marked in use. Mounting read-write
  would let macOS and Windows disagree about the same files. `ntfs-3g` refuses
  unless the hibernation file is removed. `--remove-hiberfile` does that: it
  **discards the hibernated Windows session**, so any unsaved work in that
  session is lost. The CLI therefore prompts you to type the exact volume label
  before running it, and never enables it implicitly. The safer fix is to boot
  Windows and shut it down fully (or disable Fast Startup), then reconnect.
- **Dirty.** An unclean shutdown leaves the NTFS journal unreplayed and the
  volume marked dirty. `--recover` lets `ntfs-3g` replay the journal and mount
  read-write; this mutates the filesystem. It is off by default and is not
  accompanied by a label prompt, so use it only when you understand why the
  volume is dirty. The safer fix is to let Windows run `chkdsk`.

Both flags apply only to the command you pass them to. Nothing is persisted.

## Troubleshooting

Start with `doctor`, unprivileged and then with `sudo`:

| Symptom | Meaning | Fix |
|---|---|---|
| `fuse-t: ... not found` | FUSE-T is not installed | `brew install --cask fuse-t` |
| `ntfs-3g: ... not found` | wrong install prefix | install the FUSE-T fork at `/usr/local/bin/ntfs-3g` |
| `ntfs-3g is not linked against FUSE-T` | macFUSE build or broken linkage | rebuild from `macos-fuse-t/ntfs-3g` |
| `root: running as uid ...` unprivileged | expected before the sudo re-exec | run with `sudo`, or let `mount` re-exec |
| `device-access: ... Permission denied` as root | terminal lacks FDA | grant Full Disk Access, restart the terminal |
| `device-access: ... busy` | the probed volume is mounted; not a TCC failure | unmount it before probing |
| `mount`: `Permission denied opening the disk` | FDA on the responsible terminal | same as above |
| `mount`: `volume is marked dirty` | unreplayed NTFS journal | `--recover`, or `chkdsk` on Windows |
| `mount`: `volume is hibernated` | Windows Fast Startup/hibernation | `--remove-hiberfile` (destroys the hibernated session), or boot Windows |
| `mount`: `already mounted or files ... open` | busy volume | close files/Finder windows, unmount |
| `mount`: `already a read-write mount` | another volume with the same label holds the mount point | unmount it first, then mount this volume |
| `unmount`: `still in use` | open files or Spotlight | close them; do not force |
| `unmount`: `appears to belong to another volume` | the mount is a same-label volume's; the snapshot misattributed it | unmount that mount point manually if it should be gone |
| `unmount`: `Cannot tell whether` | shared label and `pgrep` cannot identify the server | check `mount`, then unmount manually |
| `eject`: `is still mounted` | `eject` refuses only while the named volume is mounted | `unmount --keep-unmounted` first; siblings are not checked |
| `no NTFS volume matching` | wrong label/device | `ntfsmnt list` |
| `matches multiple volumes` | duplicate labels | use the device node or UUID |

Only a `Permission denied` / EPERM `device-access` failure at root points to
Full Disk Access. A `busy` result just means the probed volume is mounted.

If a mount attempt fails, check that Apple's read-only mount is back:

```bash
mount | grep HD1
# /dev/disk4s1 on /Volumes/HD1 (ntfs, local, ..., read-only, ...)
```

If it is missing, `sudo diskutil mount /dev/disk4s1` restores it. Do not run
`ntfsfix` or any other repair tool.

## Limitations

- **FUSE-T licence.** FUSE-T is **free for non-commercial use only**. Commercial
  use requires a licence from the FUSE-T project. `doctor` prints this as an
  informational line; the tool does not enforce or check licensing.
- **The mount is NFS, not a device mount.** `mount` shows `fuse-t:/HD1 on
  /Volumes/HD1 (nfs)`; `diskutil list` still lists the partition but does not
  report the NFS mount as a mount point, and an `ntfs-3g` process serves it.
  Tools that assume a local block-device mount may behave differently.
- **Apple remount race.** macOS can remount the device read-only after the tool
  has evicted its mount. The tool evicts competitors and proves writability with
  a real write, but a future macOS release could make this race harder or
  impossible to win.
- **Not tested against BitLocker-encrypted or compressed NTFS volumes.** Only
  plain NTFS volumes were exercised. Treat those layouts as unsupported until
  tested.
- **No force option for unmount.** The `unmount` command reports a busy volume
  instead of forcing it, there is no `--force`, and `SIGKILL` is never used. At
  mount time Apple's read-only mount is evicted with `diskutil unmount`,
  falling back to `umount -f`; the mount failure path likewise force-unmounts
  our own FUSE-T mount if `diskutil unmount` refuses it, before restoring the
  read-only mount — it only runs that unmount when a fresh table read shows a
  `fuse-t:` entry at the mount point (or the table cannot be read). A FUSE-T
  mount is never evicted to make room for another volume.
- **Duplicate labels.** `list` and `status` attribute a `fuse-t:/<label>` entry
  by label, so when two NTFS volumes share a label the mount may be shown on the
  wrong row. `mount`, `unmount`, and `eject` resolve it by device and refuse when
  they cannot; use the device node or UUID to name the right volume.
- **A relabelled volume's old mount is not claimed.** A FUSE-T mount records the
  label it was mounted with. If the volume has been relabelled since, its entry
  names the old label, so the tool cannot claim it: `mount` refuses at the
  occupied path, `unmount` refuses with the manual instruction above, and
  `eject` refuses rather than ejecting a disk under a live mount. Free it by
  mount point (`sudo diskutil unmount /Volumes/<old>`) and mount again.
- **Spotlight and Finder** can hold files open and block deletes or unmounts.
  The tool does not disable indexing.
- **Hibernated and dirty volumes** are refused by default; the opt-in flags
  mutate the filesystem as described above.
- **Performance** over the NFS indirection has not been benchmarked; USB speed
  is expected to dominate.
- **Unlabeled volumes are refused.** The label names the FUSE-T mount source
  and the default mount point, and two unlabeled volumes cannot be told apart.
  Relabel the volume, then mount it.
- **One volume at a time in practice.** Mounting HD2 read-write while HD1 is
  read-write is supported by the code paths but was not part of the verified
  acceptance run.
- **Intel Macs and other macOS versions** are untested. The verified target is
  Apple Silicon, macOS 26.5.2.

## Development

```bash
uv sync --extra web
uv run pytest -q
```

Without `--extra web` the FastAPI/Jinja API, page, and schema tests skip; the
service, models, and config tests still run.
The suite has 296 tests and never touches hardware. Subprocess effects
(`diskutil`, `ntfs-3g`, `mount`, `pgrep`, `kill`, `sleep`) are injected as
callables, and the mount-point creation, write probe, and ownership `stat` are
monkeypatched in tests that exercise the orchestration.

Manual acceptance on real hardware is documented in
[`tests/manual/acceptance.md`](tests/manual/acceptance.md). It covers HD1 only
and needs root, a real NTFS volume, and a Full Disk Access terminal.
`tests/manual/fuset-mount-test.sh` is the older root-only probe that predates
the CLI, kept verbatim as reference behaviour.

The mount/write/unmount path is not covered by CI. The stack itself was proven
with that root-only probe before the CLI existed; the current acceptance path is
the CLI procedure, run by hand and recorded by whoever runs it.

## Uninstall

Unmount any read-write volume first, so FUSE-T is not removed under a live NFS
mount:

```bash
ntfsmnt unmount HD1
```

Then remove the command, if you installed it with `uv tool install` or `pipx`,
and the stack if you no longer need it:

```bash
uv tool uninstall ntfsmnt                    # or: pipx uninstall ntfsmnt
brew uninstall AkshayHarti/ntfs-mounter/ntfsmnt        # if installed via the tap
brew uninstall AkshayHarti/ntfs-mounter/ntfs-3g-fuse-t # if installed via the tap
brew uninstall --cask fuse-t
```

`ntfs-3g` from the setup script was built from source and is not managed by this
repo; remove it with
the build directory's install rules if you want it gone. The build directory is
a cache, so it survives uninstalling the CLI:

```bash
rm -rf ~/Library/Caches/ntfsmnt/ntfs-3g
```

(`$XDG_CACHE_HOME/ntfsmnt/ntfs-3g` when `XDG_CACHE_HOME` is set.) Deleting the
checkout removes the CLI source and its virtual environment.
