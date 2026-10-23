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


def _failure_detail(stderr: str) -> str:
    """The most useful line from ntfs-3g/FUSE-T output, if there is one."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    for line in lines:
        lowered = line.lower()
        if any(
            word in lowered
            for word in ("can't mount", "cannot mount", "failed", "error", "not empty")
        ):
            return line[:300]
    return lines[-1][:300] if lines else ""


def classify_mount_failure(
    *, returncode: int, stderr: str, timeout: int | None = None
) -> str:
    detail = _failure_detail(stderr)
    if returncode == 0:
        # ntfs-3g exited cleanly, so "failed (exit code 0)" would be a lie: the
        # mount simply never appeared in the table. FUSE-T's NFS helper fails
        # after the parent exits, so its own message is the only clue we get.
        if timeout is None:
            message = "ntfs-3g exited successfully but the mount did not appear."
        else:
            message = (
                "ntfs-3g exited successfully but the mount did not appear "
                f"within {timeout} seconds."
            )
        return f"{message} {detail}".strip()
    text = stderr.lower()
    # Specific, actionable causes first: ntfs-3g reports hibernated and dirty
    # volumes with a trailing "Operation not permitted", so EPERM alone must not
    # win, or the user is sent to Full Disk Access for a non-permission problem.
    if "hibernat" in text:
        return (
            "The volume is hibernated. Re-run with --remove-hiberfile to discard "
            "the hibernation file and mount it."
        )
    if "unclean" in text or "dirty" in text:
        return (
            "The volume is marked dirty. Re-run with --recover to let ntfs-3g "
            "replay its journal and mount it."
        )
    if "busy" in text or "in use" in text:
        return (
            "The volume is already mounted or files on it are open. Unmount it or "
            "close those files, then try again."
        )
    if "operation not permitted" in text or "eperm" in text:
        return (
            "Permission denied opening the disk. Grant Full Disk Access to your "
            "terminal app in System Settings > Privacy & Security, then try again."
        )
    detail = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
    suffix = f": {detail}" if detail else ""
    return f"ntfs-3g failed (exit code {returncode}){suffix}"
