# Final fix report — ntfsmnt

## 2026-10-21 — C1, second pass: duplicate-label identity

**Status:** fixed.
**Commit:** `fix: resolve duplicate-label mounts by device, refuse instead of evicting`
**Tests:** `uv run pytest -q` → 139 passed.

### What was still wrong

- `volumes._belongs` attaches every `fuse-t:/<label>` entry to every device with
  that label, so `build_volumes` reports drive B (unmounted, `disk6s1`) as
  read-write at drive A's mount point.
- `unmounter._serves_volume` case (a) then accepted A's mount for B with no
  `pgrep`: `unmount /dev/disk6s1` ran `diskutil unmount /Volumes/DATA` and
  returned True.
- `mounter` treated a bare label as a candidate and evicted every candidate
  `is_ours` rejected, so `mount /dev/disk6s1` force-unmounted A's live
  read-write mount.
- The regression tests built `Volume(device=OTHER, mounts=())`, a snapshot
  `build_volumes` never produces, so they passed vacuously.

### What changed

- `identity.owner(...)` now returns `"ours" | "other" | "unknown"`: a unique
  label settles ownership without `pgrep`, a shared label needs the serving
  process, and `rc` 127 is `unknown` rather than a guess. `is_ours` is now
  strict corroboration.
- `mounter.mount_volume(..., label_shared=...)`: idempotency accepts `"ours"`,
  or `"unknown"` only with a unique label; only Apple's `/dev/...` entries are
  evicted, never a `fuse-t:` entry; a FUSE-T mount at the target is refused
  before mkdir, eviction, or ntfs-3g, naming the path and label.
- `unmounter`: `unmount_volume`, `has_mount`, and `eject_volume` take
  `label_shared`; entries are classified as ours / foreign / unclear. A foreign
  entry at the snapshot's `rw` path raises the manual instruction, and an
  unclear entry with a shared label raises instead of guessing. Apple's
  `source == device` entries are unchanged.
- Busy path: the read-only restore runs only when a fresh table read shows our
  FUSE-T mount gone; otherwise `diskutil mount` is skipped, so no
  `/Volumes/<label> 1` is left behind.
- `cli._label_shared` computes `sum(1 for v in volumes if v.label == label) > 1`
  from the collector and passes it to mount, unmount, and eject.
- README: removed the force-eviction claim, documented the refusal and the
  duplicate-label `list`/`status` attribution limitation. AGENTS.md and the
  acceptance notes were refreshed.

### Tests

Rebuilt the vacuous ones with the real `build_volumes` and the fake mount
table:

- unmount drive B: raises `UnmountFailed`; no `diskutil unmount`, `umount`, or
  `kill`; the entry stays in the table.
- unmount drive A (pgrep matches `disk4s1`): tears the mount down.
- unique label + `pgrep` rc 127: unmount proceeds.
- shared label + `pgrep` rc 127: manual-instruction error, no teardown.
- mount drive B with A live: `MountFailed` naming the path and label; zero
  mount-point creation, zero `diskutil unmount`, zero ntfs-3g calls; A's entry
  intact.
- mount drive A with A live: idempotent success, zero `diskutil` calls.
- busy path: restore skipped while the read-write mount is still in the table.
- added `owner` unit tests, CLI `label_shared` propagation tests, and eject
  guard tests in both directions.

One behaviour test was replaced, not loosened:
`test_same_label_mount_served_by_another_device_is_not_idempotent` asserted that
mounting B evicted A; that outcome is now explicitly forbidden, and the test
asserts the refusal instead.

### Verification

`uv run pytest -q` → **139 passed**.

End-to-end CLI simulation (real `build_volumes`, one `fuse-t:/DATA` entry,
`pgrep` matching only `disk4s1`):

```
unmount /dev/disk6s1 -> 1  "appears to belong to another volume with the same label 'DATA'"; no diskutil calls
mount   /dev/disk6s1 -> 1  "mount point /Volumes/DATA is already a read-write mount ..."; no diskutil, no ntfs-3g
mount   /dev/disk4s1 -> 0  "mounted DATA at /Volumes/DATA"; no diskutil calls
```

The mount table still holds drive A's entry after all three commands.

## 2026-10-21 — C1, third pass: ownership starts at the entry's own label

**Status:** fixed.
**Commit:** `fix: scope mount ownership to the entry's own label`
**Tests:** `uv run pytest -q` → 147 passed.

### What was still wrong

`identity.owner()` asked `pgrep` whether the volume's device had a serving
process, but never asked whether the *entry* named that volume at all. Its
`label` parameter was dead, so every `fuse-t:` entry in the live table was a
candidate for every volume. With HD1 (`/dev/disk4s1`, label `HD1`) and HD2
(`/dev/disk6s1`, label `HD2`) attached:

- both read-write: `unmount HD1` unmounted `/Volumes/HD1` **and**
  `/Volumes/HD2`, then restored `/dev/disk4s1`, and returned True — HD2's live
  mount was destroyed by a command that never named it;
- HD1 unmounted, HD2 read-write: `unmount HD1` still unmounted `/Volumes/HD2`
  (a unique label turned the missing `pgrep` into "ours"); with HD2 busy it also
  skipped HD1's restore and sent `SIGTERM` to the `disk4s1` server "for" a mount
  point it does not serve;
- `mount HD1` with only HD2 read-write returned `mount_point="/Volumes/HD1"`
  with zero calls — a false success, nothing mounted;
- `eject HD1` with only HD2 mounted was refused as "still mounted".

### What changed

- `identity.names_label(entry, label)` / `source_for(label)`: a `fuse-t:` entry
  claims exactly one label, and only `fuse-t:/<label>` can be this volume's
  mount. `owner()` now returns `"other"` for anything else **before** asking
  `pgrep`; the `label` parameter is live. Apple's mounts are still handled by
  `source == device.device` in the callers, unchanged.
- `unmounter._classify` and `mounter._ownership` filter candidates by that same
  source match, so a differently labelled entry cannot be classified — or
  counted as mounted, unmounted, or signalled — for this volume.
- `mounter.mount_volume`: idempotency now also requires the resolved entry's
  `mount_point` to be the target. A live mount of this label elsewhere is not a
  success; the mount falls through to the target (which still refuses if a
  FUSE-T mount occupies it). `_wait_for_mount` verifies the label too.
- `mounter._restore_read_only`: the teardown unmount now runs only when a fresh
  best-effort table read shows a `fuse-t:` entry at that path; an Apple mount,
  an empty path, or another volume's table is skipped. An unreadable table
  still attempts the unmount, so I1 is not weakened.
- `identity.is_ours` had no production caller and is deleted with its tests.
- README and AGENTS.md updated: the source is the entry's identity (new
  invariant 7), the restore teardown condition, the mount-point requirement for
  idempotency, and a limitation for a relabelled volume whose old mount names
  the previous label.

### Tests

The matrix, all built from real `build_volumes` snapshots of the live fake
table (and each verified to fail against the previous source):

- HD1 and HD2 both read-write, distinct labels: `unmount HD1` runs exactly
  `diskutil unmount /Volumes/HD1`, restores `/dev/disk4s1`, leaves HD2's entry
  in the table, sends no signal, returns True.
- HD1 unmounted, HD2 read-write: `unmount HD1` makes no calls at all; HD2 is
  untouched.
- HD1 unmounted, HD2 read-write: `mount HD1` invokes
  `ntfs-3g /dev/disk4s1 /Volumes/HD1` and never `pgrep`s `disk6s1`; HD2's entry
  is not unmounted.
- HD1 and HD2 both mounted: `mount HD1` is an idempotent success with zero
  `diskutil`/`ntfs-3g` calls.
- HD2 read-write only: `eject HD1` runs `diskutil eject disk4` instead of
  refusing.
- an entry under another label (`fuse-t:/HD1-old`) is never unmounted;
  `owner()` answers `"other"` without a `pgrep` call.
- a live mount of this label at another path does not count as idempotent: the
  target is mounted, the stale entry is left alone.
- a `fuse-t:/HD2` mount occupying HD1's target is refused with the label it
  actually carries (`not to 'HD1'`), with no eviction, mkdir, or `ntfs-3g` run.
- the failure-path restore skips the teardown when no `fuse-t:` entry is at the
  mount point, and still attempts it when the table is unreadable.
- same-label duplicate tests (unmount/eject/mount) unchanged and green.

### Verification

`uv run pytest -q` → **147 passed**.

The same matrix run against the pre-fix `owner`/`_classify` reproduces the
review's findings exactly:

```
1 unmount HD1 -> True, diskutil unmounts /Volumes/HD1 AND /Volumes/HD2, HD2 gone
2 unmount HD1 -> pgrep disk4s1, diskutil unmount /Volumes/HD2, diskutil mount disk4s1
3 mount HD1   -> MountResult('/Volumes/HD1'), 0 ntfs-3g calls
4 has_mount(HD1) -> True (eject refused)
```

With the fix: 1 touches only `/Volumes/HD1`, 2 makes no calls, 3 runs
`ntfs-3g /dev/disk4s1 /Volumes/HD1`, 4 is False.

## 2026-10-21 — third pass: a relabelled live mount is not invisible

**Status:** fixed.
**Commit:** `fix: refuse unmount and eject on an unclaimable live mount`
**Tests:** `uv run pytest -q` → 153 passed.

### What was still wrong

HD1 was mounted read-write as `fuse-t:/HD1`, then relabelled. Discovery still
reports it read-write at `/Volumes/HD1-old`, but the live entry is
`fuse-t:/HD1-old on /Volumes/HD1-old`, which `_classify` drops: the source names
another label, so the entry is nobody's candidate. `mount_points` was then
empty, so:

- `unmount HD1` returned True with zero `diskutil` calls and the CLI printed
  `unmounted HD1`, exit 0, while the volume stayed read-write at that path.
- `has_mount` was False, so `eject HD1` went on to `diskutil eject disk4` with a
  live ntfs-3g mount on it.

### What changed

- `unmounter._unclaimed_at`: the `fuse-t:` entry at the snapshot's own mount
  point whose source names a label this volume cannot claim. `_classify` is
  untouched; this is a separate, narrower question.
- `unmount_volume`: with no own mount point, an unclaimed entry at
  `volume.mount_point` raises `UnmountFailed` naming the actual path and the
  label the entry carries, with the manual `sudo diskutil unmount <path>`
  instruction. Nothing is run. A volume with nothing at its path is still a
  silent no-op.
- `has_mount`: an unclaimed entry at the snapshot's path counts as mounted (fail
  closed), so `eject` refuses instead of ejecting the parent disk. A same-label
  entry `pgrep` attributes to another disk is still `foreign` and does not count:
  ejecting this disk cannot touch that mount.
- `identity.owner` docstring now matches the code: the source match settles
  candidacy, and `pgrep` only decides between same-label candidates.
- AGENTS.md invariant 7 and the README (unmount section, limitations) describe
  the refusal; the AGENTS test count is 153.

### Tests

Snapshots built with the real `build_volumes`:

- relabelled entry (`fuse-t:/HD1-old` at `/Volumes/HD1-old`, the device at that
  path) + `unmount HD1` → `UnmountFailed` naming the path, the carried label and
  the volume label, with the manual command; zero commands run; the entry stays
  in the table.
- same snapshot + `eject HD1` → refused with the unmount-first message; zero
  `diskutil eject` calls. CLI level: exit 1, nothing on stdout, no `need_root`
  replay.
- same snapshot + CLI `unmount HD1` → exit 1, stderr only, no `unmounted HD1`.
- ordinary unmounted volume (no entry at the path) → `unmount` still returns
  True with zero calls.
- `mount HD1` on that snapshot still refuses naming the occupant (`'HD1-old'`),
  unchanged and green.

Nits:

- the empty-table restore test stays; a new
  `test_restore_leaves_an_apple_mount_at_the_mount_point_alone` reaches the
  restore with Apple's remount at the target and asserts no `diskutil unmount`.
- `test_a_live_mount_elsewhere_does_not_count_as_idempotent` now takes its
  snapshot from `build_volumes` (the discovery table) against the live table
  that only holds `/Volumes/HD1-old`.

### Verification

`uv run pytest -q` → **153 passed** (147 before; 6 new).

The four new regression tests fail against the previous `unmounter.py`; the
no-op guard passes before and after, as intended.
