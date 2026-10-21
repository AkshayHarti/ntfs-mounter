# Manual acceptance — HD1 only

This is the human-run acceptance procedure for the CLI. It exercises the real
stack (macOS, FUSE-T, ntfs-3g) on one real NTFS volume. The real effects cannot
be covered by unit tests: those inject a fake runner and never touch hardware.
This document is the procedure, not a recorded result — capture the output of
each step as you run it.

Target: **HD1** — `/dev/disk4s1`, label `HD1`, mount point `/Volumes/HD1`,
1 TB NTFS on a USB WD My Passport. Do not run these steps against HD2.

## Prerequisites

- macOS on Apple Silicon. Verified on macOS 26.5.2 (25F84).
- FUSE-T 1.2.9 (`brew install --cask fuse-t`), library at
  `/usr/local/lib/libfuse-t.dylib`.
- ntfs-3g 2022.10.3 built from <https://github.com/macos-fuse-t/ntfs-3g>,
  installed at `/usr/local/bin/ntfs-3g` and linked against `libfuse-t`.
- The terminal running these commands has **Full Disk Access** in
  System Settings → Privacy & Security → Full Disk Access, and was restarted
  after the grant. `sudo` from a terminal without FDA still gets `EPERM` on
  `/dev/disk*`; `doctor` reports that.
- Run from the repo root (`repos/ntfs-mounter`), with `uv sync` already done.
- Close Finder windows, editors, and Spotlight-indexed documents on HD1.
  A file held open on the volume makes unmount fail as "busy".

Device numbers are not stable across reboots or re-plugs. Run
`uv run ntfsmnt list` first; if HD1 is not `/dev/disk4s1`, substitute the device
node shown there everywhere below.

Exit codes: `0` success, `1` failure (message on stderr), `2` refused
confirmation.

## Step 1 — discover volumes (unprivileged)

```bash
uv run ntfsmnt list
```

Expect a table with HD1 and HD2 in state `ro` (other NTFS volumes may also be
listed):

```
DEVICE        LABEL      SIZE           STATE      MOUNT
/dev/disk4s1  HD1        1000296415232  ro         /Volumes/HD1
/dev/disk4s2  HD2        1000065728512  ro         /Volumes/HD2
```

If HD1 shows `unmounted`, mount it read-only first (`diskutil mount
/dev/disk4s1`) or mount it from Finder, then repeat.

Rollback: none — read-only.

## Step 2 — doctor without root

```bash
uv run ntfsmnt doctor; echo "exit=$?"
```

Expect `exit=1` with `macos`, `fuse-t`, `ntfs-3g`, and `licence` ok, and:

```
FAIL root: running as uid 501, not root
fix: Run via sudo from a terminal that has Full Disk Access
FAIL device-access: ntfs-3g.probe failed on /dev/disk4s1 with exit code 19: Error opening read-only '/dev/disk4s1': Permission denied
fix: Run as root via sudo from a terminal that has Full Disk Access
```

This is the expected unprivileged result, not a broken install. The exact probe
exit code can vary; the `FAIL` and the `fix:` line are what matter. The
device-access probe targets the first NTFS device that `diskutil` reports.

Rollback: none.

## Step 3 — doctor as root

Run from the **Full Disk Access terminal**:

```bash
sudo uv run ntfsmnt doctor; echo "exit=$?"
```

Expect `exit=0` with `ok` on every line once the probed volume is not mounted,
including:

```
ok root: running as root
ok device-access: /dev/disk4s1 is readable/writable
```

If `sudo: uv: command not found`, use the absolute path:
`sudo "$(command -v uv)" run ntfsmnt doctor`.

If `device-access` reports `busy`, that is **not** a TCC failure: at this point
HD1 is normally mounted read-only, so the probe's device is still in use.
Unmount it, re-run the check, then restore the read-only mount:

```bash
sudo diskutil unmount /dev/disk4s1
sudo uv run ntfsmnt doctor
sudo diskutil mount /dev/disk4s1
```

Only a `Permission denied` / EPERM result as root is the TCC/FDA signal: grant
Full Disk Access to the terminal app, quit and reopen it, and repeat. Do not
continue to Step 4 until `device-access` has passed in an unmounted state.

Rollback: none.

## Step 4 — mount HD1 read-write

```bash
uv run ntfsmnt mount HD1
```

Expect one `sudo` password prompt (the CLI re-execs itself through sudo, it does
not prompt for a volume label here), then:

```
mounted HD1 at /Volumes/HD1
```

Confirm the mount is FUSE-T, not Apple's read-only driver:

```bash
mount | grep HD1
```

Expect a line of this shape:

```
fuse-t:/HD1 on /Volumes/HD1 (nfs, nodev, nosuid, noowners)
```

Rollback: `uv run ntfsmnt unmount HD1` (Step 7). If the CLI itself is what
failed, restore the read-only mount by hand:

```bash
sudo diskutil unmount /Volumes/HD1
sudo diskutil mount /dev/disk4s1
```

## Step 5 — write as the normal user (no sudo)

This is the point of the tool: the mounted volume is writable by the invoking
user, not only by root.

```bash
ls -l /Volumes/HD1           # files already on the volume
ls -ld /Volumes/HD1          # the mount point itself
printf test > /Volumes/HD1/.ntfsmnt-acceptance
rm /Volumes/HD1/.ntfsmnt-acceptance
echo "write+delete ok"
```

Expect the mount point to be owned by your user (uid 501 on this machine), for
example:

```
drwxr-xr-x  1 akshayharti  staff  16384 Oct 21 16:00 /Volumes/HD1
```

and `write+delete ok` with no permission error. The `printf` and `rm` are run
as your normal user, with no `sudo`.

Rollback: the command deletes its own probe file. If the `rm` failed but the
write succeeded, remove only that file as root
(`sudo rm /Volumes/HD1/.ntfsmnt-acceptance`), then unmount.

## Step 6 — status

```bash
uv run ntfsmnt status HD1
```

Expect (label, state, mount point, device):

```
HD1 rw /Volumes/HD1 /dev/disk4s1
```

`uv run ntfsmnt status HD1 --json` returns the same facts as one object with
`"state": "rw"`.

Rollback: none.

## Step 7 — unmount and restore Apple's read-only mount

```bash
uv run ntfsmnt unmount HD1
mount | grep HD1
uv run ntfsmnt list
pgrep -fl ntfs-3g
```

Expect `unmounted HD1`, then Apple's read-only mount back:

```
/dev/disk4s1 on /Volumes/HD1 (ntfs, local, nodev, nosuid, read-only, noowners, noatime, fskit)
```

`ntfsmnt list` shows HD1 as `ro`, and `pgrep` shows no ntfs-3g process serving
`disk4s1` (it may print nothing at all).

Rollback: if `/Volumes/HD1` is now missing entirely, put the read-only mount
back with `sudo diskutil mount /dev/disk4s1`.

## Step 8 — keep-unmounted, then eject

```bash
uv run ntfsmnt unmount HD1 --keep-unmounted
mount | grep HD1          # expect no output
uv run ntfsmnt list       # expect HD1 unmounted, HD2 still ro
uv run ntfsmnt unmount HD2 --keep-unmounted
uv run ntfsmnt eject HD1
```

Expect `unmounted HD1` with **no** HD1 read-only mount restored and HD2 still
`ro`; then `unmounted HD2`, leaving that volume unmounted too; then
`ejected HD1`.

`eject` acts on the parent disk, so it ejects the whole USB drive. The CLI's
guard is per-volume: it refuses only when HD1 itself is still mounted, not when
a sibling is. HD2 is unmounted first so that ejecting `disk4` does not take a
mounted HD2 down with it.

Restore afterwards by re-plugging the drive, or, if it is still attached, mount
both partitions explicitly:

```bash
sudo diskutil mount /dev/disk4s1
sudo diskutil mount /dev/disk4s2
```

Expect both read-only mounts back in `ro` state in `uv run ntfsmnt list`.

Rollback: re-plugging is always safe. If HD1 itself is still mounted, `eject`
refuses and prints `HD1 is still mounted. Unmount it first, then eject.` That
guard does not cover HD2.

## Evidence to capture

Record the command, its output, and the date. Paste terminal output into the
task report; screenshots are not needed.

| # | Command | What the evidence proves |
|---|---|---|
| 1 | `uv run ntfsmnt list` | discovery finds HD1/HD2 and reports `ro` |
| 2 | `uv run ntfsmnt doctor; echo exit=$?` | unprivileged run fails only root/device-access, with actionable fixes |
| 3 | `sudo uv run ntfsmnt doctor; echo exit=$?` | the whole stack passes as root with FDA |
| 4 | `uv run ntfsmnt mount HD1` | the CLI mounts and reports the mount point |
| 4 | `mount \| grep HD1` | the mount is `fuse-t:/HD1 ... (nfs)`, not `ntfs ... read-only` |
| 5 | `ls -ld /Volumes/HD1` | mount point is owned by the invoking uid, not root |
| 5 | `printf test > /Volumes/HD1/.ntfsmnt-acceptance` then `rm /Volumes/HD1/.ntfsmnt-acceptance` | a normal user can write and delete on the volume |
| 6 | `uv run ntfsmnt status HD1` (+ `--json`) | effective state is `rw` |
| 7 | `uv run ntfsmnt unmount HD1`, `mount \| grep HD1` | read-only fskit mount restored |
| 7 | `uv run ntfsmnt list`, `pgrep -fl ntfs-3g` | state back to `ro`, no serving process left |
| 8 | `unmount --keep-unmounted` for HD1 and HD2, `mount \| grep HD1` | volumes stay unmounted, nothing silently restored |
| 8 | `uv run ntfsmnt eject HD1` | ejection succeeds on the parent disk |
| 8 | `sudo diskutil mount /dev/disk4s1`, `sudo diskutil mount /dev/disk4s2` | both read-only mounts are recoverable |

## What unit tests do not cover, and why

The 92 unit tests inject `run`, `mounts`, `sleep`, and `probe`, so they verify
command sequences and messages but never touch a device. These paths are
therefore only exercised by hand, and this run does not exercise them:

- **Hibernated volume refusal and `--remove-hiberfile`.** Producing the input
  needs a volume hibernated by Windows Fast Startup. HD1 holds live data;
  creating that state means booting Windows and risking unsynced writes.
  Unit tests cover the classifier's message and that the flag is forwarded,
  but not a real ntfs-3g refusal.
- **Dirty volume refusal and `--recover`.** Needs a volume left unclean by
  Windows, or deliberate corruption. Journal replay mutates the filesystem, so
  it is not done on HD1. Only the message/flag plumbing is unit-tested.
- **Duplicate labels.** Needs two attached NTFS volumes with the same label.
  The attached volumes (HD1, HD2, and the three on disk5) all have distinct
  labels, so ambiguity cannot be produced without renaming a real volume. Unit
  tests cover the resolver's ambiguity error with synthetic volumes.
- **Full Disk Access failure.** Needs a terminal *without* FDA. Revoking and
  re-granting TCC mid-run is disruptive, and the unprivileged `doctor` in Step
  2 already demonstrates the permission-denied path (`EPERM` on the device).
  The FDA-specific branch (root + `EPERM`) is only reachable from a
  non-FDA terminal.
- **Apple remount race.** Timing-dependent: macOS may remount read-only behind
  the tool's back. It cannot be forced on demand. Step 4's `mount` line plus
  Step 5's normal-user write are the practical evidence that the eviction
  worked on this run; they are not a guarantee for future macOS releases.
- **Physical eject and re-plug.** A human action; not scriptable.

## If something looks wrong

1. **Stop writing to the volume.** Do not run repair tools.
2. Restore Apple's read-only mount:

   ```bash
   sudo diskutil unmount /Volumes/HD1
   sudo diskutil mount /dev/disk4s1
   mount | grep HD1   # expect "ntfs ... read-only ... fskit"
   ```

3. If the mount point is stuck, unmount by device, then re-plug the drive:
   `sudo diskutil unmount /dev/disk4s1`.
4. **Never** run `ntfsfix`, `ntfsresize`, `mkntfs`, or `ntfswipe` to "clean up".
   They mutate NTFS metadata and can destroy data. The CLI never calls them, and
   neither should you.
5. If `mount` shows the `nfs` mount but writes fail, capture `mount | grep
   HD1`, `ls -l /Volumes/HD1`, `pgrep -fl ntfs-3g`, and the `mount` error, then
   unmount. The mount being present is not proof of writability; only the
   write probe is.
6. If the device node changed, trust `uv run ntfsmnt list` over this document
   and substitute the real node.
7. `doctor` is the first stop for any environment error: it separates missing
   FUSE-T, wrong ntfs-3g linkage, missing root, and TCC/FDA denial.

The older root-only probe, `tests/manual/fuset-mount-test.sh`, is kept as
reference behaviour and predates the CLI. Prefer this procedure.
