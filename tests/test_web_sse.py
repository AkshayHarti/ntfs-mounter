import asyncio
import json

import pytest

pytest.importorskip("fastapi")

from ntfsmnt.web.app import reconcile_once, stream_frames
from ntfsmnt.web.events import EventBus


def test_stream_replays_history_first():
    async def go():
        bus = EventBus()
        bus.publish("state", {"a": 1})
        frames = stream_frames(bus, last_event_id=0, ping_interval=60)
        first = await asyncio.wait_for(frames.__anext__(), 1)
        assert first == 'id: 1\nevent: state\ndata: {"a": 1}\n\n'
        await frames.aclose()

    asyncio.run(go())


def test_stream_replays_only_after_last_id():
    async def go():
        bus = EventBus()
        bus.publish("a", {})
        bus.publish("b", {})
        frames = stream_frames(bus, last_event_id=1, ping_interval=60)
        assert "event: b" in await asyncio.wait_for(frames.__anext__(), 1)
        await frames.aclose()

    asyncio.run(go())


def test_reconcile_publishes_only_on_change():
    class Fake:
        busy = False
        def __init__(self):
            self.n = 0
        def snapshot(self):
            from ntfsmnt.web.models import (DoctorStatus, HostView, Snapshot)
            self.n += 1
            return Snapshot(f"now-{self.n}", HostView(True, False, None, True, True),
                            DoctorStatus("ok", "now", ()), None, ())

    async def go():
        bus = EventBus()
        fake = Fake()
        first = await reconcile_once(fake, bus, "")
        second = await reconcile_once(fake, bus, first)
        assert second == first  # only generated_at changed, so still unchanged
        await asyncio.sleep(0)
        assert len(bus.replay(0)) == 1  # only the first snapshot was published

    asyncio.run(go())


def test_reconcile_is_skipped_while_busy():
    class Fake:
        busy = True
        snapshot = None

    async def go():
        bus = EventBus()
        assert await reconcile_once(Fake(), bus, "last") == "last"
        assert bus.replay(0) == []

    asyncio.run(go())


def test_reconcile_is_skipped_if_busy_after_snapshot():
    class Fake:
        def __init__(self):
            self.busy = False

        def snapshot(self):
            from ntfsmnt.web.models import DoctorStatus, HostView, Snapshot
            self.busy = True  # an action took the lock while we snapshot
            return Snapshot("now", HostView(True, False, None, True, True),
                            DoctorStatus("ok", "now", ()), None, ())

    async def go():
        bus = EventBus()
        assert await reconcile_once(Fake(), bus, "last") == "last"
        assert bus.replay(0) == []

    asyncio.run(go())
