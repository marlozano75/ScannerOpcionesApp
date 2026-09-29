"""Limitador de ritmo (ventana deslizante) para peticiones sujetas a pacing de IBKR."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Awaitable, Callable


class AsyncRateLimiter:
    """Permite como máximo `max_calls` llamadas por ventana de `period` segundos."""

    def __init__(
        self,
        max_calls: int,
        period: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_calls < 1 or period <= 0:
            raise ValueError("max_calls >= 1 y period > 0")
        self.max_calls = max_calls
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                await self._sleep(self.period - (now - self._calls[0]))
