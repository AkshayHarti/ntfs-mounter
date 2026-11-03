"""Narrow, expiring authority for an agent to touch one volume.

In memory, owned by the controller process. A request is not a grant; only an
approval mints one. Destructive CLI flags are not representable here at all.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from ntfsmnt.control.clock import utcnow
from ntfsmnt.volumes import Volume

MIN_TTL = 60
MAX_TTL = 1800
DEFAULT_TTL = 900
MAX_PENDING = 3
PENDING_TTL = 300

GRANTABLE = frozenset({"mount", "unmount", "eject"})
DESTRUCTIVE = frozenset({"recover", "remove_hiberfile"})


class GrantError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class VolumeBinding:
    uuid: str | None
    device: str
    label: str

    @classmethod
    def of(cls, volume: Volume) -> "VolumeBinding":
        return cls(uuid=volume.device.uuid, device=volume.device.device,
                   label=volume.device.label)

    def matches(self, volume: Volume) -> bool:
        if self.uuid is not None and volume.device.uuid is not None:
            return self.uuid == volume.device.uuid
        return self.device == volume.device.device and self.label == volume.device.label


@dataclass(frozen=True)
class GrantRequest:
    id: str
    nonce: str
    status: str
    binding: VolumeBinding
    actions: frozenset[str]
    ttl_seconds: int
    reason: str
    client: str
    created_at: datetime
    grant_id: str | None = None


@dataclass(frozen=True)
class Grant:
    id: str
    binding: VolumeBinding
    actions: frozenset[str]
    created_at: datetime
    expires_at: datetime
    reason: str
    client: str


def _normalize(actions: Sequence[str]) -> frozenset[str]:
    chosen = frozenset(actions)
    if not chosen:
        raise ValueError("at least one action is required")
    if chosen & DESTRUCTIVE:
        raise ValueError("manual_required: destructive flags are not grantable")
    unknown = chosen - GRANTABLE
    if unknown:
        raise ValueError(f"unknown action: {sorted(unknown)[0]}")
    if "mount" in chosen:
        chosen |= {"unmount"}
    return chosen


class GrantStore:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utcnow,
        max_pending: int = MAX_PENDING,
    ) -> None:
        self._clock = clock
        self._max_pending = max_pending
        self._requests: dict[str, GrantRequest] = {}
        self._grants: dict[str, Grant] = {}
        self._revoked: dict[str, datetime] = {}

    def request(
        self,
        volume: Volume,
        actions: Sequence[str],
        *,
        ttl_seconds: int = DEFAULT_TTL,
        reason: str = "",
        client: str = "mcp",
    ) -> GrantRequest:
        chosen = _normalize(actions)
        self.expire()
        binding = VolumeBinding.of(volume)
        for existing in self._requests.values():
            if existing.status == "pending" and existing.binding == binding \
                    and existing.actions == chosen:
                return existing
        pending = sum(1 for r in self._requests.values() if r.status == "pending")
        if pending >= self._max_pending:
            raise GrantError("too_many_requests")
        ttl = max(MIN_TTL, min(int(ttl_seconds), MAX_TTL))
        request = GrantRequest(
            id=f"r_{secrets.token_urlsafe(12)}",
            nonce=secrets.token_urlsafe(32),
            status="pending",
            binding=binding,
            actions=chosen,
            ttl_seconds=ttl,
            reason=reason,
            client=client,
            created_at=self._clock(),
        )
        self._requests[request.id] = request
        return request

    def pending(self, request_id: str) -> GrantRequest | None:
        return self._requests.get(request_id)

    def approve(self, request_id: str) -> Grant:
        request = self._requests.get(request_id)
        if request is None or request.status != "pending":
            raise GrantError("grant_required")
        now = self._clock()
        grant = Grant(
            id=f"g_{secrets.token_urlsafe(12)}",
            binding=request.binding,
            actions=request.actions,
            created_at=now,
            expires_at=now + timedelta(seconds=request.ttl_seconds),
            reason=request.reason,
            client=request.client,
        )
        self._grants[grant.id] = grant
        self._requests[request_id] = replace(
            request, status="approved", grant_id=grant.id
        )
        return grant

    def deny(self, request_id: str) -> None:
        request = self._requests.get(request_id)
        if request is not None and request.status == "pending":
            self._requests[request_id] = replace(request, status="denied")

    def grant(self, grant_id: str) -> Grant | None:
        self.expire()
        return self._grants.get(grant_id)

    def check(self, grant_id: str | None, volume: Volume, action: str) -> Grant:
        if grant_id is not None and grant_id in self._revoked:
            raise GrantError("grant_revoked")
        grant = self._grants.get(grant_id) if grant_id else None
        if grant is None:
            raise GrantError("grant_required")
        if grant.expires_at <= self._clock():
            raise GrantError("grant_expired")
        if not grant.binding.matches(volume):
            raise GrantError("grant_mismatch")
        if action not in grant.actions:
            raise GrantError("grant_action_mismatch")
        return grant

    def revoke(self, grant_id: str) -> bool:
        grant = self._grants.pop(grant_id, None)
        if grant is None:
            return False
        self._revoked[grant_id] = grant.expires_at
        return True

    def active(self) -> list[Grant]:
        self.expire()
        return sorted(self._grants.values(), key=lambda g: g.expires_at)

    def expire(self) -> None:
        now = self._clock()
        for grant_id, grant in list(self._grants.items()):
            if grant.expires_at <= now:
                del self._grants[grant_id]
        for request_id, request in list(self._requests.items()):
            if request.status == "pending" and \
                    request.created_at + timedelta(seconds=PENDING_TTL) <= now:
                del self._requests[request_id]
        for grant_id, expires in list(self._revoked.items()):
            if expires <= now:
                del self._revoked[grant_id]
