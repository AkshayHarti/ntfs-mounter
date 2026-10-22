import asyncio
import json
import threading
from collections import deque
from typing import Any

from ntfsmnt.web.models import ActivityEntry


def format_frame(event_id: int, event: str, data: Any) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data)}\n\n"


class ActivityLog:
    def __init__(self, limit: int = 100) -> None:
        self._items: deque[ActivityEntry] = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._next = 1

    def append(self, action, volume, ok, summary, detail=None) -> ActivityEntry:
        with self._lock:
            entry = ActivityEntry(
                id=self._next, at=_now_iso(), action=action, volume=volume,
                ok=ok, summary=summary, detail=detail,
            )
            self._next += 1
            self._items.appendleft(entry)
            return entry

    def entries(self) -> tuple[ActivityEntry, ...]:
        with self._lock:
            return tuple(self._items)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EventBus:
    def __init__(self, limit: int = 100) -> None:
        self._subs: set[asyncio.Queue[str]] = set()
        self._history: deque[str] = deque(maxlen=limit)
        self._next = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish(self, event: str, data: Any) -> int:
        # Worker threads call publish; allocate the id and record the frame
        # atomically so ids and replay order cannot diverge. Dispatch happens
        # after the lock is released.
        with self._lock:
            self._next += 1
            event_id = self._next
            frame = format_frame(event_id, event, data)
            self._history.append(frame)
        if self._loop is not None:
            try:
                self._loop.call_soon_threadsafe(self._fanout, frame)
            except RuntimeError:
                # The loop closed between the check and the hop (shutdown);
                # the frame is already in the replay ring, so drop the push.
                pass
        else:
            self._fanout(frame)
        return event_id

    def _fanout(self, frame: str) -> None:
        for queue in list(self._subs):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(frame)

    def subscribe(self) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subs.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        self._subs.discard(queue)

    def replay(self, last_id: int) -> list[str]:
        # Snapshot under the lock: a worker-thread publish mutating the deque
        # while the loop iterates it would raise RuntimeError.
        with self._lock:
            frames = list(self._history)
        if last_id <= 0:
            return frames
        return [f for f in frames if _frame_id(f) > last_id]

    def has_subscribers(self) -> bool:
        return bool(self._subs)


def _frame_id(frame: str) -> int:
    return int(frame.split(":", 1)[1].split("\n", 1)[0])
