"""Punto de entrada: compone dependencias y arranca la interfaz web local.

    python -m scanner_opciones.main [--config config/config.yaml] [--port 8000]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import uvicorn

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.config.settings import Settings, load_settings
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.errors import ConfigError
from scanner_opciones.jobs.scheduler import PeriodicRunner
from scanner_opciones.storage.db import Database
from scanner_opciones.ui.web import create_app

DEFAULT_CONFIG = Path("config/config.yaml")


def build_app(settings: Settings):
    db = Database(settings.storage.path)

    def factory(mode: AccountMode) -> IBKRGateway:
        return IBKRGateway(settings.ibkr.model_copy(update={"mode": mode}))

    service = AppService(factory(settings.ibkr.mode), db, settings)
    runner = PeriodicRunner(service.refresh_all, settings.refresh.interval_minutes * 60)
    startup_task: list[asyncio.Task] = []

    async def on_startup() -> None:
        # No bloquea la UI si TWS tarda o no está disponible
        startup_task.append(asyncio.create_task(service.start()))
        runner.start()

    async def on_shutdown() -> None:
        await runner.stop()
        for t in startup_task:
            t.cancel()
        await service.stop()
        db.close()

    return create_app(service, factory, on_startup, on_shutdown)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scanner de opciones IBKR")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(f"Error de configuración: {exc}\n"
              f"Copia config/config.example.yaml a config/config.yaml y ajústalo.", file=sys.stderr)
        return 2
    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(build_app(settings), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
