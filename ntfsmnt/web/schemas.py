from typing import Literal

from pydantic import BaseModel, Field

from ntfsmnt.web.models import (ActionError, ActionResult, ActivityEntry,
                                DoctorStatus, HostView, OperationView, Snapshot,
                                VolumeView)


class OwnerModel(BaseModel):
    uid: int
    gid: int


class HostModel(BaseModel):
    root: bool
    read_only: bool
    owner: OwnerModel | None
    fuse_t: bool
    ntfs_3g: bool


class FindingModel(BaseModel):
    severity: Literal["warning", "blocker"]
    check: str
    message: str
    fix: str | None


class DoctorModel(BaseModel):
    status: Literal["ok", "warning", "blocker"]
    checked_at: str
    findings: list[FindingModel]


class ActionModel(BaseModel):
    name: str
    enabled: bool
    reason: str | None


class OptionModel(BaseModel):
    name: str
    enabled: bool
    guarded: bool
    reason: str | None


class VolumeModel(BaseModel):
    id: str
    device: str
    label: str
    uuid: str | None
    size: int
    fs: str
    state: str
    mount_point: str | None
    driver: str | None
    pid: int | None
    flags: dict[str, bool]
    actions: list[ActionModel]
    options: list[OptionModel]


class OperationModel(BaseModel):
    id: str
    action: str
    volume: str
    started_at: str


class StateModel(BaseModel):
    generated_at: str
    host: HostModel
    doctor: DoctorModel
    operation: OperationModel | None
    volumes: list[VolumeModel]


class ActivityEntryModel(BaseModel):
    id: int
    at: str
    action: str
    volume: str
    ok: bool
    summary: str
    detail: str | None


class ActivityModel(BaseModel):
    entries: list[ActivityEntryModel]


class ActionRequest(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    size: int | None = None
    recover: bool = False
    remove_hiberfile: bool = False
    confirm: str | None = None


class ErrorModel(BaseModel):
    code: str
    message: str
    next_step: str | None = None
    detail: str | None = None
    volume: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorModel


class ResultModel(BaseModel):
    ok: bool
    message: str
    restored: bool | None = None


class ActionResultModel(BaseModel):
    result: ResultModel
    volume: VolumeModel | None = None
    operation: OperationModel | None = None
    error: ErrorModel | None = None


def _owner_model(view) -> OwnerModel | None:
    if view is None:
        return None
    return OwnerModel(uid=view.uid, gid=view.gid)


def _host_model(view: HostView) -> HostModel:
    return HostModel(
        root=view.root,
        read_only=view.read_only,
        owner=_owner_model(view.owner),
        fuse_t=view.fuse_t,
        ntfs_3g=view.ntfs_3g,
    )


def _finding_model(finding) -> FindingModel:
    return FindingModel(
        severity=finding.severity,
        check=finding.check,
        message=finding.message,
        fix=finding.fix,
    )


def _doctor_model(doctor: DoctorStatus) -> DoctorModel:
    return DoctorModel(
        status=doctor.status,
        checked_at=doctor.checked_at,
        findings=[_finding_model(f) for f in doctor.findings],
    )


def _action_model(view) -> ActionModel:
    return ActionModel(name=view.name, enabled=view.enabled, reason=view.reason)


def _option_model(view) -> OptionModel:
    return OptionModel(
        name=view.name,
        enabled=view.enabled,
        guarded=view.guarded,
        reason=view.reason,
    )


def _operation_model(view: OperationView | None) -> OperationModel | None:
    if view is None:
        return None
    return OperationModel(
        id=view.id,
        action=view.action,
        volume=view.volume,
        started_at=view.started_at,
    )


def _volume_model(view: VolumeView) -> VolumeModel:
    return VolumeModel(
        id=view.id,
        device=view.device,
        label=view.label,
        uuid=view.uuid,
        size=view.size,
        fs=view.fs,
        state=view.state,
        mount_point=view.mount_point,
        driver=view.driver,
        pid=view.pid,
        flags=dict(view.flags),
        actions=[_action_model(a) for a in view.actions],
        options=[_option_model(o) for o in view.options],
    )


def _activity_entry_model(entry: ActivityEntry) -> ActivityEntryModel:
    return ActivityEntryModel(
        id=entry.id,
        at=entry.at,
        action=entry.action,
        volume=entry.volume,
        ok=entry.ok,
        summary=entry.summary,
        detail=entry.detail,
    )


def _error_model(error: ActionError) -> ErrorModel:
    return ErrorModel(
        code=error.code,
        message=error.message,
        next_step=error.next_step,
        detail=error.detail,
        volume=error.volume,
    )


def state_model(snapshot: Snapshot) -> StateModel:
    return StateModel(
        generated_at=snapshot.generated_at,
        host=_host_model(snapshot.host),
        doctor=_doctor_model(snapshot.doctor),
        operation=_operation_model(snapshot.operation),
        volumes=[_volume_model(v) for v in snapshot.volumes],
    )


def activity_model(entries: tuple[ActivityEntry, ...]) -> ActivityModel:
    return ActivityModel(entries=[_activity_entry_model(e) for e in entries])


def result_model(result: ActionResult) -> ActionResultModel:
    return ActionResultModel(
        result=ResultModel(ok=result.ok, message=result.message, restored=result.restored),
        volume=_volume_model(result.volume) if result.volume is not None else None,
        operation=None,
        error=_error_model(result.error) if result.error is not None else None,
    )


def error_response(error: ActionError) -> ErrorResponse:
    return ErrorResponse(error=_error_model(error))
