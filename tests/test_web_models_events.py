import asyncio
import json
import sys
import threading

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


def test_bus_publish_from_worker_thread_hops_through_loop():
    async def go():
        bus = EventBus()
        queue = bus.subscribe()
        bus.bind(asyncio.get_running_loop())
        worker = threading.Thread(
            target=lambda: bus.publish("state", {"thread": True})
        )
        worker.start()
        worker.join()
        # The loop cannot run callbacks while this coroutine blocks in
        # join(), so a direct fan-out from the worker thread would already
        # have filled the queue here.
        assert queue.qsize() == 0
        frame = await asyncio.wait_for(queue.get(), 0.5)
        assert frame == 'id: 1\nevent: state\ndata: {"thread": true}\n\n'
        assert bus.replay(0) == [frame]

    asyncio.run(go())


def test_concurrent_publish_keeps_ids_unique_and_in_order():
    bus = EventBus(limit=1000)
    previous = sys.getswitchinterval()
    # Force frequent thread switches so the id allocation race would show.
    sys.setswitchinterval(1e-6)

    def publish_many():
        for i in range(100):
            bus.publish("state", {"i": i})

    workers = [threading.Thread(target=publish_many) for _ in range(8)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
    finally:
        sys.setswitchinterval(previous)
    frames = bus.replay(0)
    ids = [int(f.split("\n", 1)[0].split(": ", 1)[1]) for f in frames]
    assert ids == list(range(1, 801))


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
