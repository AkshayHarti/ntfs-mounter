#!/bin/bash
# NOTE: this script predates the `ntfsmnt` CLI. It is the proven, root-only
# probe that established FUSE-T + ntfs-3g read-write NTFS on this machine, and
# it is kept verbatim as reference behaviour. The current acceptance path is
# tests/manual/acceptance.md, which drives the CLI instead. Do not automate
# this script; run it only as root, from a terminal that has Full Disk Access.
#
# fuset-mount-test.sh — run as root from a cmux pane.
# Mount HD1 read-write via FUSE-T + ntfs-3g, verify writes, restore read-only.
set -uo pipefail

DEV=/dev/disk4s1
IDENT=disk4s1
MP=/Volumes/HD1
VOLNAME=HD1
REAL_UID=501
REAL_GID=20
REAL_USER=akshayharti
NTFS3G=/usr/local/bin/ntfs-3g
LOG="/Users/$REAL_USER/ntfsmnt-fuset-$(date +%Y%m%d-%H%M%S).log"
exec > >(tee -a "$LOG") 2>&1

say() { printf '\n=== %s ===\n' "$*"; }

echo "FUSE-T mount test — $(date)"

say "0. sanity"
[ "$(id -u)" -eq 0 ] || { echo "must run as root"; exit 1; }
VOL=$(diskutil info -plist "$DEV" 2>/dev/null | plutil -extract VolumeName raw -o - - 2>/dev/null)
echo "device $DEV volume=${VOL:-?}"
[ "$VOL" = "$VOLNAME" ] || { echo "ABORT: $DEV is not $VOLNAME"; exit 1; }
echo "ntfs-3g: $("$NTFS3G" --version 2>&1 | head -1)"
pgrep -fl "fuse-t" | head -3 || echo "no fuse-t process (may start on demand)"

say "1. unmount everything on $MP and on $DEV"
while mount | grep -qE " on $MP \(|^$DEV on "; do
  MPS=$(mount | awk -v dev="$DEV" -v mp="$MP" '$1==dev {s=index($0," on "); r=substr($0,s+4); e=index(r," ("); print (e?substr(r,1,e-1):r)}' )
  MPS="$MPS"$'\n'"$(mount | grep -F " on $MP (" | sed -e 's/^.* on //' -e 's/ (.*$//')"
  CHANGED=0
  while IFS= read -r m; do
    [ -z "$m" ] && continue
    echo "unmounting $m"
    diskutil unmount "$m" >/dev/null 2>&1 || umount "$m" >/dev/null 2>&1 || umount -f "$m" >/dev/null 2>&1
    CHANGED=1
  done <<< "$MPS"
  [ $CHANGED -eq 0 ] && break
  sleep 1
done
echo "remaining: $(mount | grep -E "HD1|disk4s1" || echo none)"

say "2. let the device settle (Apple FSKit releases it)"
sleep 3
[ -e "$DEV" ] || { echo "ABORT: $DEV disappeared"; exit 1; }

say "3. mount read-write via FUSE-T (30s watchdog)"
mkdir -p "$MP"
"$NTFS3G" "$DEV" "$MP" \
  -o "uid=$REAL_UID,gid=$REAL_GID,umask=022,local,auto_xattr,noatime,volname=$VOLNAME" &
MPID=$!
OK=0
for i in $(seq 1 30); do
  if mount | grep -qF " on $MP ("; then OK=1; break; fi
  kill -0 "$MPID" 2>/dev/null || break
  sleep 1
done
wait "$MPID" 2>/dev/null
echo "[mounted=$OK]"
mount | grep -F " on $MP (" || true

if [ $OK -eq 1 ]; then
  say "4. evict Apple's competing FSKit mount, if it came back"
  sleep 1
  mount | awk -v dev="$DEV" '$1==dev {print $3}' | while IFS= read -r m; do
    [ "$m" = "$MP" ] && continue
    echo "still mounted by Apple at: $m -> unmounting"
    diskutil unmount "$m" >/dev/null 2>&1 || umount -f "$m" >/dev/null 2>&1
  done
  mount | grep -E "HD1|disk4s1" || true

  say "5. write test as $REAL_USER"
  if sudo -u "$REAL_USER" /bin/bash -c "printf 'fuse-t rw ok %s\n' \"\$(date -u +%FT%TZ)\" > '$MP/.ntfsmnt-probe'"; then
    echo "WRITE_OK"
    ls -l "$MP/.ntfsmnt-probe"
    cat "$MP/.ntfsmnt-probe"
    rm -f "$MP/.ntfsmnt-probe" && echo "DELETE_OK"
    echo
    echo "RESULT: READ-WRITE NTFS WORKS via FUSE-T on $VOLNAME"
  else
    echo "WRITE_FAILED"
    echo "RESULT: mounted but writes failed"
  fi
  sync
  say "6. unmount our mount"
  diskutil unmount "$MP" >/dev/null 2>&1 || umount "$MP" >/dev/null 2>&1 || umount -f "$MP" >/dev/null 2>&1
else
  say "4. failure diagnostics"
  echo "--- fuse-t processes ---"; pgrep -fl "fuse-t|ntfs-3g" | head
  echo "--- last ntfs-3g/fuse-t errors ---"
  log show --last 60s --predicate 'process CONTAINS "fuse" OR process == "ntfs-3g"' --style compact 2>/dev/null | tail -15
  echo "RESULT: FUSE-T mount FAILED"
fi

say "7. restore read-only mounts"
mount | grep -qF " on $MP (" || diskutil mount "$DEV" >/dev/null 2>&1
mount | grep -qF " on /Volumes/HD2 (" || diskutil mount /dev/disk4s2 >/dev/null 2>&1
mount | grep -E "HD1|HD2" || echo "WARNING: volumes not mounted; run: diskutil mount $DEV && diskutil mount /dev/disk4s2"

echo
echo "log: $LOG"
chown "$REAL_UID:$REAL_GID" "$LOG" 2>/dev/null || true
