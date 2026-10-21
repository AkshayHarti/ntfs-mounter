import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class MountEntry:
    source: str
    mount_point: str
    fs: str
    read_only: bool


def parse_mount_table(text: str) -> list[MountEntry]:
    entries: list[MountEntry] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        source, sep, rest = line.partition(" on ")
        if not sep:
            continue
        mount_point, sep, flags_part = rest.rpartition(" (")
        if not sep:
            continue
        flags = flags_part.removesuffix(")").split(", ")
        entries.append(
            MountEntry(
                source=source,
                mount_point=mount_point,
                fs=flags[0],
                read_only="read-only" in flags,
            )
        )
    return entries


def read_mount_table() -> list[MountEntry]:
    output = subprocess.check_output(["mount"], text=True)
    return parse_mount_table(output)


def entry_for(entries: list[MountEntry], mount_point: str) -> MountEntry | None:
    for entry in entries:
        if entry.mount_point == mount_point:
            return entry
    return None
