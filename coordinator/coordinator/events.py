"""Negotiation log: every coordinator step becomes an event streamed over /ws/log."""

import asyncio
import time


class EventLog:
    def __init__(self, keep: int = 500):
        self.history: list[dict] = []
        self.keep = keep
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0

    def emit(self, kind: str, text: str, **data) -> dict:
        """kind is the log tag: coordinator, broker, match, approval, challenge, game."""
        self._seq += 1
        event = {"seq": self._seq, "ts": time.time(), "kind": kind, "text": text, "data": data}
        self.history = (self.history + [event])[-self.keep :]
        for q in self._subscribers:
            q.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)
