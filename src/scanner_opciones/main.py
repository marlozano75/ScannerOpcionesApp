"""Punto de entrada: compone dependencias y arranca la interfaz web local.

    python -m scanner_opciones.main [--config config/config.yaml] [--port 8000]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.broker.probe import detect_mode
from scanner_opciones.config.settings import Settings, load_settings
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.errors import ConfigError
from scanner_opciones.jobs.scheduler import PeriodicRunner
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, quiet_sdk_logging
from scanner_opciones.storage.db import Database
from scanner_opciones.ui.web import create_app

DEFAULT_CONFIG = Path("config/config.yaml")


def build_app(settings: Settings):
    if settings.ibkr.auto_detect_mode:
        mode = detect_mode(settings.ibkr)
        settings = settings.model_copy(update={"ibkr": settings.ibkr.model_copy(update={"mode": mode})})
    db = Database(settings.storage.path)

    tt = settings.tastytrade
    if not (tt.client_secret and tt.refresh_token):
        raise ConfigError("Faltan tastytrade.client_secret y tastytrade.refresh_token en config.yaml (IV Rank e IV Percentile)")
    md = settings.market_data
    tasty = TastytradeVolatility(           # IV Rank/Percentile, precio de contraste, velas y (market_data.source) opciones
        tt.client_secret, tt.refresh_token,
        quote_wait=md.quote_wait_seconds, settle=md.settle_seconds, option_batch_size=md.quote_batch_size,
    )

    def factory(mode: AccountMode) -> BrokerGateway:
        ibkr = IBKRGateway(settings.ibkr.model_copy(update={"mode": mode}))
        # «tastytrade»: cadena, cotizaciones, precios y ex-dividendos de tastytrade; cuenta, margen, sector y VIX de IBKR
        return HybridGateway(ibkr, tasty, md, fallback_batch=settings.refresh.batch_size) if md.source == "tastytrade" else ibkr

    service = AppService(factory(settings.ibkr.mode), db, settings, volatility=tasty, prices=tasty, candles=tasty,
                         fundamentals=tasty)
    runner = PeriodicRunner(service.refresh_periodic, settings.refresh_interval_minutes * 60)
    account_runner = PeriodicRunner(service.refresh_account, settings.refresh.account_interval_minutes * 60)
    startup_task: list[asyncio.Task] = []

    async def on_startup() -> None:
        # No bloquea la UI si TWS tarda o no está disponible
        startup_task.append(asyncio.create_task(service.start()))
        runner.start()
        account_runner.start()

    async def on_shutdown() -> None:
        await runner.stop()
        await account_runner.stop()
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
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.logging.file:
        log_path = Path(settings.logging.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(
            log_path, maxBytes=settings.logging.file_max_mb * 1024 * 1024,
            backupCount=settings.logging.file_backups, encoding="utf-8",
        ))
    logging.basicConfig(
        level=getattr(logging, settings.logging.level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    logging.getLogger("ib_async").setLevel(getattr(logging, settings.logging.ib_async_level.upper(), logging.WARNING))
    quiet_sdk_logging(getattr(logging, settings.logging.tastytrade_level.upper(), logging.WARNING))
    uvicorn.run(build_app(settings), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
