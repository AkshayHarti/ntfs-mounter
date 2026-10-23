from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from itertools import count
from threading import Lock

from ntfsmnt.cli import real_run
from ntfsmnt.diskutil import read_all_infos
from ntfsmnt.doctor import HARD_CHECK_NAMES, Check, Env, run_checks
from ntfsmnt.identity import parse_pid, pid_pattern
from ntfsmnt.mounter import MountFailed, MountResult, mount_volume
from ntfsmnt.mounttable import MountEntry, read_mount_table
from ntfsmnt.unmounter import UnmountFailed, eject_volume, unmount_volume
from ntfsmnt.volumes import Volume, build_volumes
from ntfsmnt.web.config import Settings
from ntfsmnt.web.events import ActivityLog, EventBus
from ntfsmnt.web.models import (
    ActionError, ActionResult, ActionView, DoctorStatus, Finding, HostView,
    OperationView, OptionView, OwnerView, Snapshot, VolumeView,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def volume_id(volume: Volume) -> str:
    return volume.device.uuid or f"dev-{volume.device.identifier}"


class OperationInProgress(Exception):
    pass


class _Fail(Exception):
    def __init__(self, error: ActionError) -> None:
        self.error = error


def default_discover() -> list[Volume]:
    return build_volumes(read_all_infos(), read_mount_table())


class Service:
    def __init__(
        self,
        settings: Settings,
        *,
        discover: Callable[[], list[Volume]] = default_discover,
        run: Callable[[list[str]], tuple[int, str]] = real_run,
        mounts: Callable[[], list[MountEntry]] = read_mount_table,
        sleep: Callable[[float], None] = time.sleep,
        events: EventBus | None = None,
        activity: ActivityLog | None = None,
        doctor_env: Env | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.settings = settings
        self._discover = discover
        self._run = run
        self._mounts = mounts
        self._sleep = sleep
        self.events = events or EventBus()
        self.activity_log = activity or ActivityLog()
        self._doctor = DoctorStatus(status="ok", checked_at="", findings=())
        self._doctor_env = doctor_env
        self._clock = clock
        self._lock = Lock()
        self._operation: OperationView | None = None
        self._op_ids = count(1)
        self._flags: dict[str, dict[str, bool]] = {}

    @property
    def busy(self) -> bool:
        return self._operation is not None

    def activity(self):
        return self.activity_log.entries()

    def refresh_doctor(self) -> DoctorStatus:
        env = self._doctor_env or self._default_doctor_env()
        checks = run_checks(env)
        findings = tuple(
            Finding(
                severity=(
                    "blocker"
                    if c.name in HARD_CHECK_NAMES and not c.advisory
                    else "warning"
                ),
                check=c.name, message=c.detail, fix=c.fix,
            )
            for c in checks if not c.ok
        )
        status = (
            "blocker" if any(f.severity == "blocker" for f in findings)
            else "warning" if findings else "ok"
        )
        self._doctor = DoctorStatus(status=status, checked_at=iso(self._clock()), findings=findings)
        self.events.publish("doctor", asdict(self._doctor))
        return self._doctor

    def _default_doctor_env(self) -> Env:
        from ntfsmnt.cli import _doctor_env
        return _doctor_env()

    def snapshot(self) -> Snapshot:
        volumes = self._discover()
        views = tuple(self._view(v) for v in volumes)
        return Snapshot(
            generated_at=iso(self._clock()),
            host=self._host(),
            doctor=self._doctor,
            operation=self._operation,
            volumes=views,
        )

    def _host(self) -> HostView:
        failed = {f.check for f in self._doctor.findings}
        return HostView(
            root=self.settings.root,
            read_only=self.settings.read_only,
            owner=OwnerView(*self.settings.owner) if self.settings.owner else None,
            fuse_t="fuse-t" not in failed,
            ntfs_3g="ntfs-3g" not in failed,
        )

    def _view(self, volume: Volume) -> VolumeView:
        identifier = volume_id(volume)
        driver = {"rw": "fuse-t", "ro": "fskit"}.get(volume.state)
        pid = (
            parse_pid(self._run(["pgrep", "-f", pid_pattern(volume.device.device)])[1])
            if volume.state == "rw" else None
        )
        return VolumeView(
            id=identifier,
            device=volume.device.device,
            label=volume.device.label,
            uuid=volume.device.uuid,
            size=int(volume.device.size),
            fs="ntfs",
            state=volume.state,
            mount_point=volume.mount_point,
            driver=driver,
            pid=pid,
            flags=dict(self._flags.get(identifier, {"hibernated": False, "dirty": False})),
            actions=self._actions(volume),
            options=self._options(volume),
        )

    def _doctor_blocker(self):
        return next(
            (f for f in self._doctor.findings
             if f.severity == "blocker"
             and f.check in {"fuse-t", "ntfs-3g", "device-access"}),
            None,
        )

    def _reason(self) -> str | None:
        if self.settings.read_only:
            return "Read-only mode: restart with `sudo ntfsmnt web` to enable actions."
        blocker = self._doctor_blocker()
        if blocker is not None:
            fix = f" {blocker.fix}" if blocker.fix else ""
            return f"Host check failed: {blocker.message}.{fix}".strip()
        return None

    def _actions(self, volume: Volume) -> tuple[ActionView, ...]:
        mode = self._reason()
        label_missing = not volume.device.label
        return (
            ActionView("mount", mode is None and not label_missing and volume.state != "rw",
                       mode or ("volume has no label; relabel it before mounting" if label_missing
                                else "already writable" if volume.state == "rw" else None)),
            ActionView("unmount", mode is None and volume.state == "rw",
                       mode or ("not mounted read-write" if volume.state != "rw" else None)),
            ActionView("eject", mode is None and volume.state in ("ro", "unmounted"),
                       mode or ("unmount it first" if volume.state == "rw" else None)),
        )

    def _options(self, volume: Volume) -> tuple[OptionView, ...]:
        mode = self._reason()
        available = mode is None and volume.state != "rw"
        return (
            OptionView("recover", available, False, mode),
            OptionView("remove_hiberfile", available, True,
                       mode or "Windows hibernation state will be discarded."),
        )

    @contextmanager
    def operation(self, action: str, volume: str) -> Iterator[OperationView]:
        if not self._lock.acquire(blocking=False):
            raise OperationInProgress
        try:
            op = OperationView(
                id=f"op-{next(self._op_ids)}", action=action, volume=volume,
                started_at=iso(self._clock()),
            )
            self._operation = op
            self.events.publish("operation", asdict(op))
            yield op
        finally:
            self._operation = None
            self.events.publish("operation", None)
            self._lock.release()

    def _resolve(self, target: str, label: str, size: int | None = None) -> tuple[Volume, list[Volume]]:
        volumes = self._discover()
        matches = [v for v in volumes if volume_id(v) == target]
        if not matches:
            raise _Fail(ActionError("volume_not_found", f"No volume with id {target}.",
                                    404, "The drive may have been replugged; rescan."))
        if len(matches) > 1:
            raise _Fail(ActionError("ambiguous_volume",
                                    "More than one volume has this identity.",
                                    409, "Replug the drive or rescan."))
        volume = matches[0]
        if volume.device.label != label or (
            size is not None and int(volume.device.size) != size
        ):
            raise _Fail(ActionError(
                "volume_changed",
                f"The drive at that slot is now {volume.device.label!r}, not {label!r}.",
                409, "Rescan and choose the drive again."))
        return volume, volumes

    def _guard(self) -> ActionError | None:
        if self.settings.read_only:
            code = "owner_unresolved" if self.settings.root else "not_root"
            detail = (
                "This process is root but SUDO_UID/SUDO_GID were not captured."
                if self.settings.root
                else "The service is not running as root."
            )
            return ActionError(
                code, "Actions are disabled in read-only mode.", 403,
                "Restart with `sudo ntfsmnt web`.", detail=detail,
            )
        blocker = self._doctor_blocker()
        if blocker is not None:
            return ActionError(
                "doctor_blocker", f"Host check failed: {blocker.check}.", 409,
                blocker.fix or "Run the doctor checks and fix the host.",
                detail=blocker.message,
            )
        return None

    def mount(self, target, *, label, size=None, recover=False,
              remove_hiberfile=False, confirm=None):
        if (error := self._guard()) is not None:
            return self._result("mount", error, label)
        if not label:
            # A label is the mount point name and the typed confirmation, so a
            # bare volume can still be unmounted or ejected but never mounted.
            return self._result("mount", ActionError(
                "invalid_request", "A non-empty label is required to mount.", 422,
                "Give the volume a label, or use the CLI."), label)
        try:
            with self.operation("mount", label):
                try:
                    volume, volumes = self._resolve(target, label, size)
                except _Fail as exc:
                    return self._result("mount", exc.error, label)
                if remove_hiberfile and confirm != label:
                    error = ActionError(
                        "confirmation_mismatch",
                        f"Type {label!r} to confirm removing the hibernation file.",
                        400, "The label must match exactly.")
                    return self._result("mount", error, label)
                try:
                    mount_volume(
                        volume, uid=self.settings.owner[0], gid=self.settings.owner[1],
                        run=self._run, mounts=self._mounts, sleep=self._sleep,
                        recover=recover, remove_hiberfile=remove_hiberfile,
                        label_shared=self._shared(volume, volumes),
                    )
                except MountFailed as exc:
                    return self._mount_failure(exc, volume)
                self._flags.pop(volume_id(volume), None)
                return self._ok("mount", volume, f"{volume.device.label} is writable.", None)
        except OperationInProgress:
            return ActionResult(
                ok=False, message="Another operation is in progress.", restored=None,
                volume=None, error=ActionError(
                    "operation_in_progress", "Another operation is in progress.", 409,
                    "Wait for it to finish."))

    @staticmethod
    def _unmount_error(exc: UnmountFailed, label: str) -> ActionError:
        """Map a failed teardown to the error the client shows."""
        text = str(exc).lower()
        busy = "busy" in text or "in use" in text
        return ActionError(
            "busy" if busy else "action_failed", str(exc),
            409 if busy else 500,
            "Close anything using it, then retry." if busy else None,
            detail=str(exc), volume=label)

    def unmount(self, target, *, label, size=None):
        if (error := self._guard()) is not None:
            return self._result("unmount", error, label)
        try:
            with self.operation("unmount", label):
                try:
                    volume, volumes = self._resolve(target, label, size)
                except _Fail as exc:
                    return self._result("unmount", exc.error, label)
                if volume.state != "rw":
                    return self._result("unmount", ActionError(
                        "not_mounted", f"{label} is not mounted read-write.", 409,
                        "Nothing to unmount."), label)
                try:
                    restored = unmount_volume(
                        volume, run=self._run, mounts=self._mounts, sleep=self._sleep,
                        keep_unmounted=False, label_shared=self._shared(volume, volumes),
                    )
                except UnmountFailed as exc:
                    return self._result(
                        "unmount", self._unmount_error(exc, label), label)
                if not restored:
                    return self._result("unmount", ActionError(
                        "action_failed",
                        f"{label} was unmounted, but the read-only mount could not be restored.",
                        500, f"Run sudo diskutil mount {volume.device.device}.",
                        detail="unmount succeeded; restore failed"), label, restored=False)
                return self._ok("unmount", volume, f"{label} is read-only.", True)
        except OperationInProgress:
            return ActionResult(
                ok=False, message="Another operation is in progress.", restored=None,
                volume=None, error=ActionError(
                    "operation_in_progress", "Another operation is in progress.", 409,
                    "Wait for it to finish."))

    def eject(self, target, *, label, size=None):
        if (error := self._guard()) is not None:
            return self._result("eject", error, label)
        try:
            with self.operation("eject", label):
                try:
                    volume, volumes = self._resolve(target, label, size)
                except _Fail as exc:
                    return self._result("eject", exc.error, label)
                if volume.state == "rw":
                    return self._result("eject", ActionError(
                        "busy", f"{label} is mounted read-write.", 409,
                        "Unmount it first, then eject."), label)
                if volume.state == "ro":
                    # The read-only mount is Apple's; an Eject click means the
                    # whole disk goes, so tear the read-only mount down first
                    # and do not put it back.
                    try:
                        unmount_volume(
                            volume, run=self._run, mounts=self._mounts,
                            sleep=self._sleep, keep_unmounted=True,
                            label_shared=self._shared(volume, volumes),
                        )
                    except UnmountFailed as exc:
                        return self._result(
                            "eject", self._unmount_error(exc, label), label)
                try:
                    eject_volume(
                        volume, run=self._run, mounts=self._mounts, sleep=self._sleep,
                        label_shared=self._shared(volume, volumes),
                    )
                except UnmountFailed as exc:
                    return self._result("eject", ActionError(
                        "action_failed", str(exc), 500, None,
                        detail=str(exc), volume=label), label)
                message = f"{label} ejected."
                entry = self.activity_log.append("eject", label, True, message)
                self.events.publish("activity", asdict(entry))
                return ActionResult(True, message, None, None, None)
        except OperationInProgress:
            return ActionResult(
                ok=False, message="Another operation is in progress.", restored=None,
                volume=None, error=ActionError(
                    "operation_in_progress", "Another operation is in progress.", 409,
                    "Wait for it to finish."))

    def _current_view(self, identifier: str):
        for candidate in self._discover():
            if volume_id(candidate) == identifier:
                return self._view(candidate)
        return None

    def _result(self, action, error, label, restored=None):
        entry = self.activity_log.append(action, label, False, error.message, error.detail)
        self.events.publish("activity", asdict(entry))
        return ActionResult(ok=False, message=error.message, restored=restored,
                            volume=None, error=error)

    def _ok(self, action, volume, message, restored):
        view = self._current_view(volume_id(volume)) or self._view(volume)
        entry = self.activity_log.append(action, view.label, True, message)
        self.events.publish("activity", asdict(entry))
        return ActionResult(ok=True, message=message, restored=restored,
                            volume=view, error=None)

    def _mount_failure(self, exc, volume):
        text = str(exc).lower()
        restored = True if "was restored" in text else False if "attempted" in text else None
        if "hibernat" in text:
            code, status, message, step = (
                "blocked_hibernated", 422,
                f"{volume.device.label} is hibernated by Windows.",
                "Mount with hiberfile removal, or shut Windows down fully.")
            self._flags.setdefault(volume_id(volume), {})["hibernated"] = True
        elif "dirty" in text or "unclean" in text:
            code, status, message, step = (
                "blocked_dirty", 422, f"{volume.device.label} is marked dirty.",
                "Mount with journal recovery, or run a Windows chkdsk.")
            self._flags.setdefault(volume_id(volume), {})["dirty"] = True
        elif "busy" in text or "in use" in text:
            code, status, message, step = (
                "busy", 409, f"{volume.device.label} is in use.",
                "Close anything using it, or unmount it first.")
        elif "permission" in text or "full disk access" in text:
            code, status, message, step = (
                "permission_denied", 403, "Permission denied opening the disk.",
                "Grant Full Disk Access to your terminal, then retry.")
        else:
            code, status, message, step = (
                "action_failed", 500, str(exc), None)
        error = ActionError(code, message, status, step, detail=str(exc),
                            volume=volume.device.label)
        entry = self.activity_log.append("mount", volume.device.label, False,
                                         message, str(exc))
        self.events.publish("activity", asdict(entry))
        view = self._current_view(volume_id(volume)) or self._view(volume)
        return ActionResult(False, message, restored, view, error)

    def _shared(self, volume, volumes):
        return sum(1 for v in volumes if v.device.label == volume.device.label) > 1
