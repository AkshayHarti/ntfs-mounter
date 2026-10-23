import platform
from collections.abc import Callable
from dataclasses import dataclass

from ntfsmnt import paths

FUSE_T_LIB = "/usr/local/lib/libfuse-t.dylib"
NTFS_3G = paths.NTFS3G
NTFS_3G_PROBE = paths.NTFS3G_PROBE

HARD_CHECK_NAMES = frozenset({"fuse-t", "ntfs-3g", "root", "device-access"})


@dataclass(frozen=True)
class Env:
    path_exists: Callable[[str], bool]
    run: Callable[[list[str]], tuple[int, str]]
    euid: int
    linkage: Callable[[str], str]
    first_device: Callable[[], str | None]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    fix: str | None = None
    # A failed check that is expected in normal operation: the probed device is
    # mounted, so the probe reports busy. It still fails the CLI doctor (the
    # access cannot be verified yet) but must not disable web actions.
    advisory: bool = False


def _check_macos() -> Check:
    detail = f"macOS {platform.mac_ver()[0]} on {platform.machine()}"
    return Check("macos", True, detail)


def _check_fuse_t(env: Env) -> Check:
    if env.path_exists(FUSE_T_LIB):
        return Check("fuse-t", True, f"FUSE-T installed at {FUSE_T_LIB}")
    return Check(
        "fuse-t",
        False,
        f"{FUSE_T_LIB} not found",
        "brew install --cask fuse-t",
    )


def _check_ntfs_3g(env: Env) -> Check:
    brew_leg = "brew tap AkshayHarti/ntfs-mounter https://github.com/AkshayHarti/ntfs-mounter && brew install AkshayHarti/ntfs-mounter/ntfs-3g-fuse-t"
    if not env.path_exists(NTFS_3G):
        if NTFS_3G.startswith("/opt/homebrew"):
            fix = f"{brew_leg}, or run ./scripts/setup.sh from a checkout"
        else:
            fix = (
                "Run ./scripts/setup.sh from a checkout to build ntfs-3g from "
                f"macos-fuse-t/ntfs-3g, then install it at {NTFS_3G}"
            )
        return Check("ntfs-3g", False, f"{NTFS_3G} not found", fix)
    if "libfuse-t" not in env.linkage(NTFS_3G):
        if NTFS_3G.startswith("/opt/homebrew"):
            fix = (
                f"{NTFS_3G} is Homebrew's macFUSE-linked ntfs-3g-mac. "
                f"{brew_leg} (uninstall ntfs-3g-mac first), or build to "
                "/usr/local/bin with ./scripts/setup.sh"
            )
        else:
            fix = (
                "Rebuild ntfs-3g from macos-fuse-t/ntfs-3g so it links "
                "against libfuse-t"
            )
        return Check(
            "ntfs-3g",
            False,
            "ntfs-3g is not linked against FUSE-T",
            fix,
        )
    return Check("ntfs-3g", True, "ntfs-3g links against libfuse-t")


def _check_root(env: Env) -> Check:
    if env.euid == 0:
        return Check("root", True, "running as root")
    return Check(
        "root",
        False,
        f"running as uid {env.euid}, not root",
        "Run via sudo from a terminal that has Full Disk Access",
    )


PERMISSION_MARKERS = (
    "permission denied",
    "eacces",
    "operation not permitted",
    "eperm",
)


def _probe_guidance(env: Env, stderr: str) -> str | None:
    lowered = stderr.lower()
    if any(marker in lowered for marker in PERMISSION_MARKERS):
        if env.euid == 0:
            return (
                "Grant Full Disk Access to your terminal in System Settings > "
                "Privacy & Security, then retry"
            )
        return "Run as root via sudo from a terminal that has Full Disk Access"
    if "busy" in lowered:
        return "The volume is mounted; unmount it first, then retry"
    return None


def _probe_detail(device: str, rc: int, stderr: str) -> str:
    detail = f"ntfs-3g.probe failed on {device} with exit code {rc}"
    for line in stderr.splitlines():
        if line.strip():
            return f"{detail}: {line.strip()}"
    return detail


def _check_device_access(env: Env) -> Check:
    device = env.first_device()
    if device is None:
        return Check(
            "device-access",
            True,
            "no NTFS device attached; device access check skipped",
        )
    rc, stderr = env.run([NTFS_3G_PROBE, "--readwrite", device])
    if rc == 0:
        return Check("device-access", True, f"{device} is readable/writable")
    detail = _probe_detail(device, rc, stderr)
    return Check(
        "device-access", False, detail, _probe_guidance(env, stderr),
        advisory="busy" in stderr.lower(),
    )


def _check_licence() -> Check:
    return Check(
        "licence",
        True,
        "FUSE-T is free for non-commercial use",
    )


def run_checks(env: Env) -> list[Check]:
    return [
        _check_macos(),
        _check_fuse_t(env),
        _check_ntfs_3g(env),
        _check_root(env),
        _check_device_access(env),
        _check_licence(),
    ]


def hard_failures(checks: list[Check]) -> list[Check]:
    return [c for c in checks if c.ok is False and c.name in HARD_CHECK_NAMES]
