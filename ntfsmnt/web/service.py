from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from itertools import count
from threading import Lock

from ntfsmnt.cli import real_run
from ntfsmnt.diskutil import read_all_infos
from ntfsmnt.doctor import HARD_CHECK_NAMES, Check, Env, run_checks
from ntfsmnt.identity import parse_pid, pid_pattern
from ntfsmnt.mounttable import MountEntry, read_mount_table
from ntfsmnt.volumes import Volume, build_volumes
from ntfsmnt.web.config import Settings
from ntfsmnt.web.events import ActivityLog, EventBus
from ntfsmnt.web.models import (
    ActionView, DoctorStatus, Finding, HostView, OperationView, OptionView,
    OwnerView, Snapshot, VolumeView,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def volume_id(volume: Volume) -> str:
    return volume.device.uuid or f"dev-{volume.device.identifier}"


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
                severity="blocker" if c.name in HARD_CHECK_NAMES else "warning",
                check=c.name, message=c.detail, fix=c.fix,
            )
            for c in checks if not c.ok
        )
        status = (
            "blocker" if any(f.severity == "blocker" for f in findings)
            else "warning" if findings else "ok"
        )
        self._doctor = DoctorStatus(status=status, checked_at=iso(self._clock()), findings=findings)
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
            ActionView("eject", mode is None and volume.state == "unmounted",
                       mode or ("unmount it first" if volume.state != "unmounted" else None)),
        )

    def _options(self, volume: Volume) -> tuple[OptionView, ...]:
        mode = self._reason()
        available = mode is None and volume.state != "rw"
        return (
            OptionView("recover", available, False, mode),
            OptionView("remove_hiberfile", available, True,
                       mode or "Windows hibernation state will be discarded."),
        )
