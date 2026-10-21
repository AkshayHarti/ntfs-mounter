NTFS3G_BIN = "/usr/local/bin/ntfs-3g"


class ConfirmationRequired(Exception):
    """Raised when an operation needs explicit user confirmation."""


def build_mount_args(
    device: str,
    mount_point: str,
    *,
    uid: int,
    gid: int,
    label: str,
    recover: bool = False,
    remove_hiberfile: bool = False,
) -> list[str]:
    options = (
        f"uid={uid},gid={gid},umask=022,local,auto_xattr,noatime,volname={label}"
    )
    if recover:
        options += ",recover"
    if remove_hiberfile:
        options += ",remove_hiberfile"
    return [NTFS3G_BIN, device, mount_point, "-o", options]


def classify_mount_failure(*, returncode: int, stderr: str) -> str:
    text = stderr.lower()
    if "operation not permitted" in text or "eperm" in text:
        return (
            "Permission denied opening the disk. Grant Full Disk Access to your "
            "terminal app in System Settings > Privacy & Security, then try again."
        )
    if "hibernat" in text:
        return (
            "The volume is hibernated. Re-run with --remove-hiberfile to discard "
            "the hibernation file and mount it."
        )
    if "busy" in text or "in use" in text:
        return (
            "The volume is already mounted or files on it are open. Unmount it or "
            "close those files, then try again."
        )
    if "unclean" in text or "dirty" in text:
        return (
            "The volume is marked dirty. Re-run with --recover to let ntfs-3g "
            "replay its journal and mount it."
        )
    detail = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    suffix = f": {detail}" if detail else ""
    return f"ntfs-3g failed (exit code {returncode}){suffix}"
