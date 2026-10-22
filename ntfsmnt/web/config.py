import hashlib
import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Settings:
    token: str
    host: str = "127.0.0.1"
    port: int = 0
    owner: tuple[int, int] | None = None
    root: bool = False
    read_only: bool = True
    open_browser: bool = False
    allowed_hosts: frozenset[str] = frozenset()
    allowed_origins: frozenset[str] = frozenset()
    cookie_name: str = "ntfsmnt_session"


def _parse_owner(text: str) -> tuple[int, int]:
    uid, sep, gid = text.partition(":")
    if not sep:
        raise ValueError(f"owner must be uid:gid, got {text!r}")
    try:
        return int(uid), int(gid)
    except ValueError as exc:
        raise ValueError(f"owner must be uid:gid, got {text!r}") from exc


def _env_owner(environ: Mapping[str, str]) -> tuple[int, int] | None:
    try:
        return int(environ["SUDO_UID"]), int(environ["SUDO_GID"])
    except (KeyError, ValueError):
        return None


def settings_from(
    *,
    port: int,
    owner: str | None,
    environ: Mapping[str, str],
    euid: int,
    token: str | None = None,
    open_browser: bool = False,
) -> Settings:
    resolved = _parse_owner(owner) if owner is not None else _env_owner(environ)
    root = euid == 0
    return Settings(
        token=token or secrets.token_urlsafe(32),
        port=port,
        owner=resolved,
        root=root,
        read_only=(not root) or resolved is None,
        open_browser=open_browser,
    )


def with_port(settings: Settings, port: int) -> Settings:
    return replace(
        settings,
        port=port,
        allowed_hosts=frozenset({f"127.0.0.1:{port}", f"localhost:{port}"}),
        allowed_origins=frozenset(
            {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
        ),
    )


def cookie_value(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def cookie_matches(token: str, presented: str | None) -> bool:
    if presented is None:
        return False
    expected = cookie_value(token).encode("utf-8")
    try:
        candidate = presented.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(expected, candidate)
