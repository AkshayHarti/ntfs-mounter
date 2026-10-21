from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from ntfsmnt.models import Device
from ntfsmnt.mounttable import MountEntry


@dataclass(frozen=True)
class Volume:
    device: Device
    mounts: tuple[MountEntry, ...]

    @property
    def state(self) -> Literal["rw", "ro", "unmounted"]:
        if not self.mounts:
            return "unmounted"
        if any(m.fs == "nfs" and m.source.startswith("fuse-t:") for m in self.mounts):
            return "rw"
        return "ro"

    @property
    def mount_point(self) -> str | None:
        return self.mounts[0].mount_point if self.mounts else None


class VolumeError(Exception):
    pass


class NoSuchVolume(VolumeError):
    pass


class AmbiguousVolume(VolumeError):
    def __init__(self, candidates: Sequence[Volume]):
        self.candidates = list(candidates)
        names = ", ".join(v.device.identifier for v in self.candidates)
        super().__init__(f"ambiguous volume: {names}")


def _belongs(entry: MountEntry, device: Device) -> bool:
    return (
        entry.source == device.device
        or (bool(device.label) and entry.source == f"fuse-t:/{device.label}")
        or (device.mount_point is not None and entry.mount_point == device.mount_point)
    )


def build_volumes(infos: Sequence[Device], mounts: Sequence[MountEntry]) -> list[Volume]:
    return [
        Volume(device=device, mounts=tuple(m for m in mounts if _belongs(m, device)))
        for device in infos
    ]


def resolve(query: str, volumes: Sequence[Volume]) -> Volume:
    strategies: tuple[Callable[[Volume], bool], ...] = (
        lambda v: v.device.identifier == query,
        lambda v: query == f"/dev/{v.device.identifier}",
        lambda v: bool(v.device.uuid) and v.device.uuid == query,
        lambda v: bool(v.device.label) and v.device.label == query,
        lambda v: v.mount_point == query,
    )
    for strategy in strategies:
        matches = [v for v in volumes if strategy(v)]
        if len(matches) > 1:
            raise AmbiguousVolume(matches)
        if matches:
            return matches[0]
    raise NoSuchVolume(f"no such volume: {query}")
