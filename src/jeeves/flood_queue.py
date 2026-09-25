"""Outbound IRC flood queue (issue #74).

Pace PRIVMSG once on a dedicated thread so the chair receive/handle loop
never blocks on multi-line !list / !help replies.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

log = logging.getLogger("jeeves.flood_queue")


class OutboundFloodQueue:
    """Queue (target, text) → send_fn with flood_s between sends."""

    def __init__(
        self,
        send_fn: Callable[[str, str], None],
        *,
        flood_s: float = 0.8,
        name: str = "jeeves-flood",
    ):
        self._send = send_fn
        self.flood_s = float(flood_s)
        self._q: queue.Queue[tuple[str, str] | None] = queue.Queue()
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._loop, name=name, daemon=True)
        self.sent = 0
        self.errors = 0

    def start(self) -> None:
        if self._t.is_alive():
            return
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, name=self._t.name, daemon=True)
        self._t.start()

    def stop(self, *, timeout: float = 2.0) -> None:
        self._stop.set()
        try:
            self._q.put_nowait(None)
        except Exception:
            pass
        if self._t.is_alive():
            self._t.join(timeout=timeout)

    def put(self, target: str, text: str) -> None:
        self._q.put((target, text))

    def pending(self) -> int:
        return self._q.qsize()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if item is None:
                continue
            target, text = item
            try:
                self._send(target, text)
                self.sent += 1
            except Exception as e:
                self.errors += 1
                log.warning("flood_send_fail target=%s err=%s", target, type(e).__name__)
            if self.flood_s > 0:
                time.sleep(self.flood_s)
