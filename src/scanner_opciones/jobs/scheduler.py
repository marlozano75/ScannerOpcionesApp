"""Bucle de refresco periódico cada X minutos (configurable)."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

log = logging.getLogger(__name__)


class PeriodicRunner:
    """Ejecuta `action` cada `interval_seconds`. Un fallo no mata el bucle."""

    def __init__(
        self,
        action: Callable[[], Awaitable[object]],
        interval_seconds: float,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.action = action
        self.interval = interval_seconds
        self._sleep = sleep
        self._task: asyncio.Task | None = None

    async def _loop(self) -> None:
        while True:
            await self._sleep(self.interval)
            try:
                await self.action()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - el bucle debe sobrevivir
                log.exception("Fallo en el refresco periódico")

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
