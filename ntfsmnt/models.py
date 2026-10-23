from dataclasses import dataclass


@dataclass(frozen=True)
class Device:
    identifier: str
    device: str
    label: str
    uuid: str
    size: int
    fs_type: str
    mount_point: str | None
