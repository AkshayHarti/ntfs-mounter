import plistlib
import subprocess

from ntfsmnt.models import Device


def parse_info_plist(raw: bytes) -> Device:
    info = plistlib.loads(raw)
    return Device(
        identifier=info["DeviceIdentifier"],
        device=info["DeviceNode"],
        label=info.get("VolumeName") or info.get("MediaName", ""),
        uuid=info.get("VolumeUUID", ""),
        size=info["Size"],
        fs_type=info.get("Content") or info["FilesystemType"],
        mount_point=info.get("MountPoint"),
    )


def read_info(identifier: str) -> Device:
    raw = subprocess.check_output(["diskutil", "info", "-plist", identifier])
    return parse_info_plist(raw)


def parse_list_plist(raw: bytes) -> list[str]:
    listing = plistlib.loads(raw)
    identifiers: list[str] = []
    for disk in listing.get("AllDisksAndPartitions", []):
        for partition in disk.get("Partitions", []):
            if (partition.get("Content") or "").lower() == "windows_ntfs":
                identifiers.append(partition["DeviceIdentifier"])
    return identifiers


def read_all_infos() -> list[Device]:
    raw = subprocess.check_output(["diskutil", "list", "-plist"])
    return [read_info(identifier) for identifier in parse_list_plist(raw)]
