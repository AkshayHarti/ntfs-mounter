from dataclasses import dataclass, field


@dataclass(frozen=True)
class Finding:
    severity: str
    check: str
    message: str
    fix: str | None = None


@dataclass(frozen=True)
class DoctorStatus:
    status: str
    checked_at: str
    findings: tuple[Finding, ...] = ()


@dataclass(frozen=True)
class OwnerView:
    uid: int
    gid: int


@dataclass(frozen=True)
class HostView:
    root: bool
    read_only: bool
    owner: OwnerView | None
    fuse_t: bool
    ntfs_3g: bool


@dataclass(frozen=True)
class OperationView:
    id: str
    action: str
    volume: str
    started_at: str


@dataclass(frozen=True)
class ActionView:
    name: str
    enabled: bool
    reason: str | None = None


@dataclass(frozen=True)
class OptionView:
    name: str
    enabled: bool
    guarded: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class VolumeView:
    id: str
    device: str
    label: str
    uuid: str
    size: int
    fs: str
    state: str
    mount_point: str | None
    driver: str | None
    pid: int | None
    flags: dict[str, bool] = field(default_factory=dict)
    actions: tuple[ActionView, ...] = ()
    options: tuple[OptionView, ...] = ()


@dataclass(frozen=True)
class Snapshot:
    generated_at: str
    host: HostView
    doctor: DoctorStatus
    operation: OperationView | None
    volumes: tuple[VolumeView, ...]


@dataclass(frozen=True)
class ActivityEntry:
    id: int
    at: str
    action: str
    volume: str
    ok: bool
    summary: str
    detail: str | None = None


@dataclass(frozen=True)
class ActionError:
    code: str
    message: str
    status: int
    next_step: str | None = None
    detail: str | None = None
    volume: str | None = None


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    message: str
    restored: bool | None
    volume: VolumeView | None
    error: ActionError | None = None
