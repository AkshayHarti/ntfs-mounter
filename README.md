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
| FUSE-T | 1.2.9, `/usr/local/lib/libfuse-t.dylib` | `brew install --cask fuse-t` |
| ntfs-3g | 2022.10.3, `/usr/local/bin/ntfs-3g` | must link `libfuse-t` |
| Terminal | Full Disk Access granted | see [TCC / Full Disk Access](#tcc--full-disk-access) |

`ntfs-3g` here is the FUSE-T fork from
<https://github.com/macos-fuse-t/ntfs-3g>, not the macFUSE build and not a
Homebrew build. It is not installed by this repo; build it from that fork so it
links against `libfuse-t`, install it at `/usr/local/bin/ntfs-3g`, and check:

```bash
otool -L /usr/local/bin/ntfs-3g | grep libfuse-t
uv run ntfsmnt doctor
```

`doctor` reports a missing FUSE-T, an `ntfs-3g` that is not linked against
FUSE-T, a missing root, and a device that cannot be opened.

## Install

From a checkout:

```bash
git clone git@github.com:AkshayHarti/ntfs-mounter.git
cd ntfs-mounter
uv sync
uv run ntfsmnt list
```

To put the `ntfsmnt` command on your `PATH`:

```bash
uv tool install --editable .
ntfsmnt list
```

The installed command re-execs through `sudo` using the interpreter of the
environment it was installed into, so it keeps working outside the checkout.

## Commands

```
ntfsmnt list [--json]
ntfsmnt doctor [--json]
ntfsmnt status <volume> [--json]
ntfsmnt mount <volume> [--recover] [--remove-hiberfile] [--json]
ntfsmnt unmount <volume> [--keep-unmounted] [--json]
ntfsmnt eject <volume> [--json]
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

Volumes served by FUSE-T are NFS mounts, so `diskutil list` does not show them;
`ntfsmnt list` combines `diskutil` device information with the live mount table.

### `doctor`

```bash
uv run ntfsmnt doctor
```

Checks, in order: macOS/architecture (informational), FUSE-T library present,
`ntfs-3g` present and linked against FUSE-T, running as root, and whether the
first NTFS device can be opened read-write with `ntfs-3g.probe`. The licence
line is informational.

Every failing check prints a `fix:` line. Exit code is `1` when any hard check
(`fuse-t`, `ntfs-3g`, `root`, `device-access`) fails, `0` otherwise.

Run it unprivileged to see the environment, and with `sudo` from the same
terminal to check root and device access:

```bash
uv run ntfsmnt doctor          # root and device-access FAIL is expected
sudo uv run ntfsmnt doctor     # every check should be ok
```

If `sudo` cannot find `uv`, use `sudo "$(command -v uv)" run ntfsmnt doctor`.

### `status`

```bash
uv run ntfsmnt status HD1
# HD1 rw /Volumes/HD1 /dev/disk4s1
```

Prints label, effective state, mount point, and device.

### `mount`

```bash
uv run ntfsmnt mount HD1
```

This is the command that makes a volume writable. It:

1. resolves the volume and refuses if the label is ambiguous;
2. returns success immediately if a FUSE-T mount already serves it;
3. creates the mount point if needed;
4. unmounts whoever holds the device — normally Apple's read-only mount;
5. waits for the device node to reappear (10 one-second attempts);
6. runs `ntfs-3g <device> <mount point>` with
   `uid=<you>,gid=<your group>,umask=022,local,auto_xattr,noatime,volname=<label>`;
7. waits up to 30 seconds for the `fuse-t:` NFS mount to appear, and evicts a
   competing Apple mount if it reappeared at a different mount point;
8. writes, reads back, and deletes `.ntfsmnt-probe` inside the volume;
9. prints `mounted HD1 at /Volumes/HD1`.

If any step fails, Apple's read-only mount is restored before the command exits
`1` with the reason. A mount that exists but cannot be written is a failure, not
a success.

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
on the same physical drive disappears with it. The volume must already be
unmounted; `eject` refuses otherwise. Re-plug the drive — or, if it is still
attached, run `diskutil mount /dev/disk4s1` — to get the read-only mounts back.

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

`status` — one object of the same shape.

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

`unmount` and `eject` — `{"ok": true}`.

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
- **Never** forces a busy unmount. It reports the mount point and exits `1`.
- **Never** guesses between volumes with the same label; it lists candidates and
  stops.
- **Never** leaves a failed mount half-applied: it restores Apple's read-only
  mount before exiting non-zero. Restoration is attempted, not re-verified.
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
| `device-access: ... busy` | the volume is mounted | unmount it before probing |
| `mount`: `Permission denied opening the disk` | FDA on the responsible terminal | same as above |
| `mount`: `volume is marked dirty` | unreplayed NTFS journal | `--recover`, or `chkdsk` on Windows |
| `mount`: `volume is hibernated` | Windows Fast Startup/hibernation | `--remove-hiberfile` (destroys the hibernated session), or boot Windows |
| `mount`: `already mounted or files ... open` | busy volume | close files/Finder windows, unmount |
| `unmount`: `still in use` | open files or Spotlight | close them; do not force |
| `eject`: `is still mounted` | `eject` needs an unmounted volume | `unmount --keep-unmounted` first |
| `no NTFS volume matching` | wrong label/device | `ntfsmnt list` |
| `matches multiple volumes` | duplicate labels | use the device node or UUID |

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
  /Volumes/HD1 (nfs)`, `diskutil list` does not show it, and an `ntfs-3g`
  process serves it. Tools that assume a local block-device mount may behave
  differently.
- **Apple remount race.** macOS can remount the device read-only after the tool
  has evicted its mount. The tool evicts competitors and proves writability with
  a real write, but a future macOS release could make this race harder or
  impossible to win.
- **Not tested against BitLocker-encrypted or compressed NTFS volumes.** Only
  plain NTFS volumes were exercised. Treat those layouts as unsupported until
  tested.
- **No force path.** Busy volumes are reported, never forced. There is no
  `--force`, and `SIGKILL` is never used.
- **Spotlight and Finder** can hold files open and block deletes or unmounts.
  The tool does not disable indexing.
- **Hibernated and dirty volumes** are refused by default; the opt-in flags
  mutate the filesystem as described above.
- **Performance** over the NFS indirection has not been benchmarked; USB speed
  is expected to dominate.
- **One volume at a time in practice.** Mounting HD2 read-write while HD1 is
  read-write is supported by the code paths but was not part of the verified
  acceptance run.
- **Intel Macs and other macOS versions** are untested. The verified target is
  Apple Silicon, macOS 26.5.2.

## Development

```bash
uv sync
uv run pytest -q
```

The suite has 92 tests and never touches hardware. Subprocess effects
(`diskutil`, `ntfs-3g`, `mount`, `pgrep`, `kill`, `sleep`) are injected as
callables, and the mount-point creation and write probe are monkeypatched in
tests that exercise the orchestration.

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
uv run ntfsmnt unmount HD1
```

Then remove the command, if you installed it with `uv tool install`, and the
stack if you no longer need it:

```bash
uv tool uninstall ntfsmnt
brew uninstall --cask fuse-t
```

`ntfs-3g` was built from source and is not managed by this repo; remove it with
the build directory's install rules if you want it gone. Deleting the checkout
removes the CLI source and its virtual environment.
