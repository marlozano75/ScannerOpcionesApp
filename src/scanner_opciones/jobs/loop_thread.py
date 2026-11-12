"""Hilo con su propio bucle de eventos para los jobs (refresco, actualización diaria, conexión con el broker).

Hasta 2026-11-12 los jobs compartían el bucle de eventos con la interfaz web: cada tramo síncrono de un refresco (construir y guardar miles de
snapshots, sincronizar el catálogo...) dejaba las páginas esperando, y el refresco dura ~3 min de cada 15. Con los jobs en este hilo la interfaz
tiene su bucle libre; las dos partes solo se reparten la CPU (el intérprete alterna entre hilos cada `SWITCH_INTERVAL_SECONDS`).
"""
from __future__ import annotations

import asyncio
import logging
import sys
import threading
from typing import Awaitable, Optional

log = logging.getLogger(__name__)

STOP_TIMEOUT_SECONDS = 10.0
# Cada cuánto cede el intérprete el turno a otro hilo que lo espera (por defecto 5 ms). Una página suelta el turno en cada fila que lee de SQLite y,
# con un job ocupando la CPU, tardaba hasta 5 ms en recuperarlo: miles de filas = varios segundos (medido con un job al 100 % de CPU: Scanner
# 3,6-7 s con 5 y 1 ms, 0,35 s con 0,3 ms; sin el hilo de jobs esperaba 3,8 s). Solo actúa cuando hay dos hilos compitiendo.
SWITCH_INTERVAL_SECONDS = 0.0003


class JobsLoop:
    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> asyncio.AbstractEventLoop:
        """Arranca el hilo y devuelve su bucle de eventos, ya en marcha."""
        if self.loop is not None:
            return self.loop
        sys.setswitchinterval(SWITCH_INTERVAL_SECONDS)
        ready = threading.Event()

        def run() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self.loop = loop
            ready.set()
            try:
                loop.run_forever()
                pending = asyncio.all_tasks(loop)       # lo que quede a medias al parar se cancela sin ruido
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:  # noqa: BLE001 - el cierre no debe dejar una traza a medias
                log.exception("Fallo al cerrar el bucle de los jobs")
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, name="jobs", daemon=True)
        self._thread.start()
        ready.wait()
        return self.loop

    def run(self, coro) -> Awaitable:
        """Ejecuta la corrutina en el bucle de los jobs; se espera desde otro bucle (el de la interfaz)."""
        return asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self.loop))

    def stop(self) -> None:
        loop, thread = self.loop, self._thread
        if loop is None or thread is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(STOP_TIMEOUT_SECONDS)
        self.loop = self._thread = None
