from datetime import datetime, timedelta, timezone

import pytest

from ntfsmnt.control.grants import (
    DEFAULT_TTL,
    MAX_TTL,
    MIN_TTL,
    GrantError,
    GrantStore,
    VolumeBinding,
)
from tests.web_helpers import make_volume


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 3, 10, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def store():
    return GrantStore(clock=Clock())


def request(store, volume=None, actions=("mount",), **kw):
    return store.request(
        volume or make_volume(), list(actions),
        reason=kw.pop("reason", "copy footage"),
        client=kw.pop("client", "Claude Desktop"),
        **kw,
    )


def test_binding_uses_uuid_when_both_sides_have_one():
    binding = VolumeBinding.of(make_volume(uuid="UUID-1", label="HD1"))

    assert binding.matches(make_volume(uuid="UUID-1", label="RENAMED", identifier="disk9s1"))
    assert not binding.matches(make_volume(uuid="UUID-2"))


def test_binding_falls_back_to_device_and_label():
    binding = VolumeBinding.of(make_volume(uuid=None, label="HD1", identifier="disk4s1"))

    assert binding.matches(make_volume(uuid=None, label="HD1", identifier="disk4s1"))
    assert not binding.matches(make_volume(uuid=None, label="HD1", identifier="disk9s1"))


def test_request_rejects_destructive_actions(store):
    for action in ("recover", "remove_hiberfile"):
        with pytest.raises(ValueError, match="manual_required"):
            request(store, actions=(action,))


def test_request_rejects_an_unknown_action(store):
    with pytest.raises(ValueError):
        request(store, actions=("repair",))


def test_request_clamps_ttl(store):
    assert request(store, volume=make_volume(uuid="U1"), ttl_seconds=5).ttl_seconds == MIN_TTL
    assert request(store, volume=make_volume(uuid="U2"), ttl_seconds=99_999).ttl_seconds == MAX_TTL
    assert request(store, volume=make_volume(uuid="U3")).ttl_seconds == DEFAULT_TTL


def test_approve_mints_a_grant_and_never_reuses_the_request_id(store):
    pending = request(store)

    grant = store.approve(pending.id)

    assert grant.id != pending.id
    assert grant.actions == frozenset({"mount", "unmount"})
    assert store.pending(pending.id).status == "approved"
    assert store.grant(grant.id) == grant


def test_mount_implies_unmount(store):
    pending = request(store, actions=("mount",))

    grant = store.approve(pending.id)

    assert store.check(grant.id, make_volume(), "unmount").id == grant.id


def test_check_rejects_wrong_action(store):
    grant = store.approve(request(store, actions=("unmount",)).id)

    with pytest.raises(GrantError) as exc:
        store.check(grant.id, make_volume(), "mount")

    assert exc.value.code == "grant_action_mismatch"


def test_check_rejects_another_volume(store):
    grant = store.approve(request(store).id)

    with pytest.raises(GrantError) as exc:
        store.check(grant.id, make_volume(uuid="UUID-2", label="OTHER"), "mount")

    assert exc.value.code == "grant_mismatch"


def test_check_rejects_expired(store):
    clock = Clock()
    store = GrantStore(clock=clock)
    grant = store.approve(request(store, ttl_seconds=60).id)
    clock.advance(61)

    with pytest.raises(GrantError) as exc:
        store.check(grant.id, make_volume(), "mount")

    assert exc.value.code == "grant_expired"


def test_pending_request_expires_without_another_store_call():
    clock = Clock()
    store = GrantStore(clock=clock)
    pending = request(store)
    clock.advance(301)

    assert store.pending(pending.id) is None


def test_check_rejects_revoked(store):
    grant = store.approve(request(store).id)

    assert store.revoke(grant.id) is True
    with pytest.raises(GrantError) as exc:
        store.check(grant.id, make_volume(), "mount")

    assert exc.value.code == "grant_revoked"


def test_check_without_a_grant_id_is_grant_required(store):
    with pytest.raises(GrantError) as exc:
        store.check(None, make_volume(), "mount")

    assert exc.value.code == "grant_required"


def test_pending_cap_and_dedupe(store):
    first = request(store, actions=("mount",))
    second = request(store, volume=make_volume(uuid="UUID-2", label="HD2"), actions=("mount",))
    third = request(store, volume=make_volume(uuid="UUID-3", label="HD3"), actions=("eject",))

    same = request(store, actions=("mount", "unmount"))
    assert same.id == first.id
    assert same.nonce == first.nonce

    with pytest.raises(GrantError) as exc:
        request(store, volume=make_volume(uuid="UUID-4", label="HD4"), actions=("mount",))

    assert exc.value.code == "too_many_requests"
    assert {first.id, second.id, third.id}


def test_deny_marks_the_request_denied_and_blocks_approval(store):
    pending = request(store)

    store.deny(pending.id)

    assert store.pending(pending.id).status == "denied"
    with pytest.raises(GrantError):
        store.approve(pending.id)
    assert store.active() == []


def test_active_prunes_expired(store):
    clock = Clock()
    store = GrantStore(clock=clock)
    grant = store.approve(request(store, ttl_seconds=60).id)
    assert [g.id for g in store.active()] == [grant.id]

    clock.advance(61)

    assert store.active() == []
    assert store.grant(grant.id) is None


def test_pending_requests_age_out_so_the_cap_cannot_wedge(store):
    clock = Clock()
    store = GrantStore(clock=clock)
    for i in range(3):
        request(store, volume=make_volume(uuid=f"UUID-{i}", label=f"HD{i}"))

    clock.advance(301)

    request(store, volume=make_volume(uuid="UUID-9", label="HD9"))
