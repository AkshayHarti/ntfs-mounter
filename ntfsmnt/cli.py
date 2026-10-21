"""Command line interface.

Read-only subcommands (``list``, ``status``, ``doctor``) run unprivileged.
Mutating ones re-exec through sudo via ``need_root``, but only after the volume
has been resolved and any destructive option has been confirmed, so a typo or a
refused confirmation never pays for a privilege prompt.
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence

from ntfsmnt.diskutil import read_all_infos
from ntfsmnt.doctor import Check, Env, hard_failures, run_checks
from ntfsmnt.identity import parse_pid, pid_pattern
from ntfsmnt.mounter import MountFailed, MountResult, mount_volume
from ntfsmnt.mounttable import read_mount_table
from ntfsmnt.privileged import need_root
from ntfsmnt.unmounter import (
    UnmountFailed,
    eject_volume,
    has_mount,
    unmount_volume,
)
from ntfsmnt.volumes import (
    AmbiguousVolume,
    NoSuchVolume,
    Volume,
    VolumeError,
    build_volumes,
    resolve,
)

HEADERS = ("DEVICE", "LABEL", "SIZE", "STATE", "MOUNT")


def real_run(argv: list[str]) -> tuple[int, str]:
    """Run a command the way every injected runner does: never raise."""
    try:
        proc = subprocess.run(argv, capture_output=True, text=True)
    except OSError as exc:
        return (127, str(exc))
    return (proc.returncode, (proc.stdout or "") + (proc.stderr or ""))


def _discover() -> list[Volume]:
    return build_volumes(read_all_infos(), read_mount_table())


def _as_dict(volume: Volume) -> dict:
    return {
        "device": volume.device.device,
        "label": volume.device.label,
        "uuid": volume.device.uuid,
        "size": int(volume.device.size),
        "state": volume.state,
        "mount_point": volume.mount_point,
    }


def render(volumes: Sequence[Volume], as_json: bool) -> str:
    if as_json:
        return json.dumps([_as_dict(volume) for volume in volumes])
    rows = [
        [
            volume.device.device,
            volume.device.label,
            str(volume.device.size),
            volume.state,
            volume.mount_point or "-",
        ]
        for volume in volumes
    ]
    widths = [max(len(row[i]) for row in [HEADERS, *rows]) for i in range(len(HEADERS))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
        for row in [HEADERS, *rows]
    )


def _lookup(query: str, volumes: Sequence[Volume]) -> Volume | None:
    """Resolve ``query`` or report why it failed; ``None`` means return 1."""
    try:
        return resolve(query, volumes)
    except AmbiguousVolume as exc:
        print(f"ntfsmnt: '{query}' matches multiple volumes:", file=sys.stderr)
        for candidate in exc.candidates:
            print(
                f"{candidate.device.label} {candidate.device.device} "
                f"{candidate.mount_point or '-'}",
                file=sys.stderr,
            )
    except NoSuchVolume:
        print(f"ntfsmnt: no NTFS volume matching '{query}'", file=sys.stderr)
    return None


def _confirm(label: str) -> None:
    # stdout is reserved for the command's output (JSON with --json), so the
    # prompt goes to stderr and `input()` is called without one of its own.
    print(f"Type the volume label ({label}) to confirm: ", end="", file=sys.stderr)
    try:
        answer = input()
    except EOFError:
        print(
            "ntfsmnt: cannot confirm without a terminal; refusing to continue",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    if answer != label:
        print(f"ntfsmnt: confirmation failed for '{label}'", file=sys.stderr)
        raise SystemExit(2)


def _invoking_ids() -> tuple[int, int]:
    """The user's ids, surviving the sudo re-exec that made us root."""
    return (_env_id("SUDO_UID", os.getuid), _env_id("SUDO_GID", os.getgid))


def _env_id(name: str, fallback: Callable[[], int]) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return fallback()


def _doctor_env() -> Env:
    return Env(
        path_exists=os.path.exists,
        run=real_run,
        euid=os.geteuid(),
        linkage=lambda path: real_run(["otool", "-L", path])[1],
        first_device=lambda: next((d.device for d in read_all_infos()), None),
    )


def _print_checks(checks: Sequence[Check]) -> None:
    for check in checks:
        status = "ok" if check.ok else "FAIL"
        print(f"{status} {check.name}: {check.detail}")
        if check.fix:
            print(f"fix: {check.fix}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ntfsmnt", description="Mount NTFS volumes read-write on macOS."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("list", "list NTFS volumes"),
        ("doctor", "check the host environment"),
    ):
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="show one volume")
    status.add_argument("label")
    status.add_argument("--json", action="store_true")

    mount = sub.add_parser("mount", help="mount a volume read-write")
    mount.add_argument("label")
    mount.add_argument("--recover", action="store_true")
    mount.add_argument("--remove-hiberfile", action="store_true")
    mount.add_argument("--json", action="store_true")
    mount.add_argument("--confirmed", action="store_true", help=argparse.SUPPRESS)

    unmount = sub.add_parser("unmount", help="restore Apple's read-only mount")
    unmount.add_argument("label")
    unmount.add_argument("--keep-unmounted", action="store_true")
    unmount.add_argument("--json", action="store_true")

    eject = sub.add_parser("eject", help="eject an unmounted volume")
    eject.add_argument("label")
    eject.add_argument("--json", action="store_true")

    return parser


def _cmd_list(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    print(render(collector(), args.json))
    return 0


def _cmd_status(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    volume = _lookup(args.label, collector())
    if volume is None:
        return 1
    driver = {"rw": "fuse-t", "ro": "fskit", "unmounted": "-"}[volume.state]
    pid = (
        parse_pid(real_run(["pgrep", "-f", pid_pattern(volume.device.device)])[1])
        if volume.state == "rw"
        else None
    )
    if args.json:
        print(json.dumps({**_as_dict(volume), "driver": driver, "pid": pid}))
    else:
        print(
            f"{volume.device.label} {volume.state} {driver} "
            f"{volume.mount_point or '-'} {volume.device.device} "
            f"{'-' if pid is None else pid}"
        )
    return 0


def _cmd_doctor(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    checks = run_checks(_doctor_env())
    if args.json:
        print(
            json.dumps(
                [
                    {"name": c.name, "ok": c.ok, "detail": c.detail, "fix": c.fix}
                    for c in checks
                ]
            )
        )
    else:
        _print_checks(checks)
    return 1 if hard_failures(checks) else 0


def _label_shared(volume: Volume, volumes: Sequence[Volume]) -> bool:
    """Does another NTFS volume in the snapshot carry this volume's label?

    A label is only ambiguous when it is shared. When it is unique, a
    ``fuse-t:/<label>`` entry belongs to this volume even if ``pgrep`` cannot
    answer; when it is shared, only the serving process can decide.
    """
    return sum(1 for v in volumes if v.device.label == volume.device.label) > 1


def _cmd_mount(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    volumes = collector()
    volume = _lookup(args.label, volumes)
    if volume is None:
        return 1
    label_shared = _label_shared(volume, volumes)
    label = volume.device.label
    # The sudo re-exec keeps only this argv, so every flag must be forwarded.
    flags = ["--recover"] if args.recover else []
    if args.remove_hiberfile:
        flags.append("--remove-hiberfile")
    if args.json:
        flags.append("--json")
    # --confirmed is forwarded to the sudo child, so it only counts when we are
    # that child: an unprivileged run with --confirmed must still prompt.
    confirmed = args.confirmed and os.geteuid() == 0
    if args.remove_hiberfile and not confirmed:
        # Only --remove-hiberfile discards data; --recover must stay non-interactive.
        _confirm(label)
        confirmed = True
    if confirmed:
        flags.append("--confirmed")
    need_root(["mount", volume.device.device, *flags])
    uid, gid = _invoking_ids()
    try:
        result: MountResult = mount_volume(
            volume,
            uid=uid,
            gid=gid,
            run=real_run,
            mounts=read_mount_table,
            sleep=time.sleep,
            recover=args.recover,
            remove_hiberfile=args.remove_hiberfile,
            label_shared=label_shared,
        )
    except MountFailed as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"mount_point": result.mount_point, "pid": result.pid}))
    else:
        print(f"mounted {label} at {result.mount_point}")
    return 0


def _cmd_unmount(
    args: argparse.Namespace, collector: Callable[[], list[Volume]]
) -> int:
    volumes = collector()
    volume = _lookup(args.label, volumes)
    if volume is None:
        return 1
    label_shared = _label_shared(volume, volumes)
    flags = ["--keep-unmounted"] if args.keep_unmounted else []
    if args.json:
        flags.append("--json")
    need_root(["unmount", volume.device.device, *flags])
    try:
        restored = unmount_volume(
            volume,
            run=real_run,
            mounts=read_mount_table,
            sleep=time.sleep,
            keep_unmounted=args.keep_unmounted,
            label_shared=label_shared,
        )
    except UnmountFailed as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True, "restored": restored}))
    elif restored:
        print(f"unmounted {volume.device.label}")
    else:
        print(
            f"ntfsmnt: unmounted {volume.device.label} (read-only restore failed; "
            f"run sudo diskutil mount {volume.device.device})",
            file=sys.stderr,
        )
    return 0 if restored else 1


def _cmd_eject(args: argparse.Namespace, collector: Callable[[], list[Volume]]) -> int:
    volumes = collector()
    volume = _lookup(args.label, volumes)
    if volume is None:
        return 1
    label_shared = _label_shared(volume, volumes)
    # The snapshot can be stale and a label is not an identity: ask the live
    # table and the serving processes before paying for a privilege prompt.
    if has_mount(
        volume, run=real_run, mounts=read_mount_table, label_shared=label_shared
    ):
        print(
            f"ntfsmnt: {volume.device.label} is still mounted. "
            "Unmount it first, then eject.",
            file=sys.stderr,
        )
        return 1
    flags = ["--json"] if args.json else []
    need_root(["eject", volume.device.device, *flags])
    try:
        eject_volume(
            volume,
            run=real_run,
            mounts=read_mount_table,
            sleep=time.sleep,
            label_shared=label_shared,
        )
    except UnmountFailed as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps({"ok": True}))
    else:
        print(f"ejected {volume.device.label}")
    return 0


COMMANDS = {
    "list": _cmd_list,
    "status": _cmd_status,
    "doctor": _cmd_doctor,
    "mount": _cmd_mount,
    "unmount": _cmd_unmount,
    "eject": _cmd_eject,
}


def main(
    argv: list[str] | None = None,
    *,
    collector: Callable[[], list[Volume]] | None = None,
) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args, collector or _discover)
    except VolumeError as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"ntfsmnt: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
