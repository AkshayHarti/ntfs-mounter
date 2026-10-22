import asyncio
import json

from ntfsmnt.web.events import ActivityLog, EventBus
from ntfsmnt.web.models import ActionError, ActionResult


def test_activity_is_newest_first_and_capped():
    log = ActivityLog(limit=2)
    log.append("mount", "HD1", True, "writable")
    log.append("unmount", "HD2", True, "read-only")
    third = log.append("eject", "HD1", False, "busy", "still mounted")
    assert [e.id for e in log.entries()] == [third.id, third.id - 1]
    assert log.entries()[0].detail == "still mounted"


def test_bus_publish_increments_and_formats_sse():
    bus = EventBus()
    assert bus.publish("state", {"a": 1}) == 1
    assert bus.publish("ping", {}) == 2
    assert bus.replay(0) == [
        'id: 1\nevent: state\ndata: {"a": 1}\n\n',
        "id: 2\nevent: ping\ndata: {}\n\n",
    ]


def test_bus_replay_starts_after_last_id():
    bus = EventBus()
    bus.publish("a", {})
    bus.publish("b", {})
    assert len(bus.replay(1)) == 1
    assert "event: b" in bus.replay(1)[0]


def test_bus_ring_keeps_last_hundred():
    bus = EventBus()
    for i in range(105):
        bus.publish("state", {"i": i})
    assert len(bus.replay(0)) == 100
    assert "id: 105" in bus.replay(0)[-1]


def test_subscriber_receives_published_frame():
    async def go():
        bus = EventBus()
        queue = bus.subscribe()
        bus.publish("state", {"ok": True})
        frame = await asyncio.wait_for(queue.get(), 1)
        assert json.loads(frame.split("data: ", 1)[1]) == {"ok": True}
        assert bus.has_subscribers() is True
        bus.unsubscribe(queue)
        assert bus.has_subscribers() is False

    asyncio.run(go())


def test_action_error_and_result_defaults():
    error = ActionError(code="busy", message="HD1 is in use.", status=409)
    assert error.next_step is None and error.volume is None
    result = ActionResult(ok=False, message="HD1 is in use.", restored=None,
                          volume=None, error=error)
    assert result.error.code == "busy"
