"""Limitador de ritmo (ventana deslizante) para peticiones sujetas a pacing de IBKR."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Awaitable, Callable, Optional


class AsyncRateLimiter:
    """Permite como máximo `max_calls` llamadas por ventana de `period` segundos."""

    def __init__(
        self,
        max_calls: int,
        period: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_wait: Optional[Callable[[float], None]] = None,
    ) -> None:
        if max_calls < 1 or period <= 0:
            raise ValueError("max_calls >= 1 y period > 0")
        self.max_calls = max_calls
        self.period = period
        self._clock = clock
        self._sleep = sleep
        self._on_wait = on_wait          # se avisa al empezar a esperar (con los segundos de espera)
        self._wait_until = 0.0           # instante (según `clock`) hasta el que se espera; 0 = sin espera
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
                    self._wait_until = 0.0
                    return
                delay = self.period - (now - self._calls[0])
                if self._wait_until == 0.0 and self._on_wait is not None:
                    self._on_wait(delay)             # primera espera de este episodio
                self._wait_until = now + delay
                await self._sleep(delay)

    def wait_remaining(self) -> float:
        """Segundos que faltan para que la petición en espera pueda salir (0 si nadie espera)."""
        if self._wait_until == 0.0:
            return 0.0
        return max(0.0, self._wait_until - self._clock())
