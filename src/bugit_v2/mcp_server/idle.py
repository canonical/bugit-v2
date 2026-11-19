"""Exit when nothing has talked to the daemon for a while.

Under systemd socket activation the next connection simply restarts us, so an
idle daemon costs nothing.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send


class IdleTracker:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.active = 0
        self._last_activity = clock()

    def idle_seconds(self) -> float:
        return self._clock() - self._last_activity

    def wrap(self, app: ASGIApp) -> ASGIApp:
        async def middleware(scope: Scope, receive: Receive, send: Send) -> None:
            if scope["type"] not in ("http", "websocket"):
                await app(scope, receive, send)
                return
            self.active += 1
            try:
                await app(scope, receive, send)
            finally:
                self.active -= 1
                self._last_activity = self._clock()

        return middleware


async def watch_idle(
    server: Any,
    tracker: IdleTracker,
    timeout: float,
    is_busy: Callable[[], bool],
    poll: float = 5.0,
) -> None:
    if timeout <= 0:
        return
    while True:
        await asyncio.sleep(poll)
        if tracker.active == 0 and not is_busy() and tracker.idle_seconds() >= timeout:
            server.should_exit = True
            return
