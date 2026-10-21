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
