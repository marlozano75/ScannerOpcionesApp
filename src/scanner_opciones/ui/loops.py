"""Reparto de las peticiones entre el bucle de eventos de la interfaz y el de los jobs (`jobs/loop_thread.py`).

- **GET/HEAD** (páginas, fragmentos, gráficos): se atienden en el bucle de la interfaz, que ya no espera a los tramos síncronos del refresco. Leen
  la base por una conexión propia (`Database.split_reader`) y, si necesitan al broker, pasan por `AppService.on_jobs`.
- **Lo demás** (POST: guardar perfiles, cargar ficheros, cambiar de cuenta...): se ejecuta entero en el bucle de los jobs, como antes del reparto,
  así que todo lo que modifica el estado del servicio sigue pasando en un solo hilo.

Sin bucle de jobs (tests, `create_app` sin `jobs`) no hace nada.
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger(__name__)

READ_METHODS = ("GET", "HEAD")


class LoopSplit:
    """Middleware ASGI. Va por dentro de la compresión, que así se hace siempre en el bucle de la interfaz."""

    def __init__(self, app, service) -> None:
        self.app, self.service = app, service

    async def __call__(self, scope, receive, send) -> None:
        jobs = self.service.jobs_loop
        if scope["type"] != "http" or jobs is None or asyncio.get_running_loop() is jobs:
            await self.app(scope, receive, send)
        elif scope["method"] in READ_METHODS:
            await self._read(scope, receive, send)
        else:
            await self._on_jobs(jobs, scope, receive, send)

    async def _read(self, scope, receive, send) -> None:
        started = False

        async def tracked(message) -> None:
            nonlocal started
            started = True
            await send(message)

        try:
            try:
                await self.app(scope, receive, tracked)
            except RuntimeError as exc:
                # El hilo de los jobs cambió un diccionario o conjunto mientras la página lo recorría («changed size during iteration»).
                # Es raro y pasajero: una lectura se puede repetir sin más (si aún no se ha enviado nada).
                if started or "during iteration" not in str(exc):
                    raise
                log.info("Lectura repetida (%s): los datos cambiaron mientras se generaba la página", scope.get("path"))
                await self.app(scope, receive, tracked)
        finally:
            self.service.settle_db()        # una escritura suelta de la página no debe dejar bloqueada la base para el refresco

    async def _on_jobs(self, jobs, scope, receive, send) -> None:
        web = asyncio.get_running_loop()

        async def receive_from_web():
            return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(receive(), web))

        async def send_from_web(message) -> None:
            await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(send(message), web))

        await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(self.app(scope, receive_from_web, send_from_web), jobs))
