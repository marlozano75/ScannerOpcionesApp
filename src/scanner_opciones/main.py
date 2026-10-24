"""Punto de entrada: compone dependencias y arranca la interfaz web local.

    python -m scanner_opciones.main [--config config/config.yaml] [--port 8000]
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path

import uvicorn

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.base import BrokerGateway
from scanner_opciones.broker.hybrid_gateway import HybridGateway
from scanner_opciones.broker.ibkr_gateway import IBKRGateway
from scanner_opciones.broker.probe import detect_mode, port_open
from scanner_opciones.config.settings import Settings, load_settings
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.errors import ConfigError
from scanner_opciones.jobs.scheduler import PeriodicRunner
from scanner_opciones.market.hours import MarketCalendar
from scanner_opciones.marketdata.edgar import EdgarFinancialsProvider
from scanner_opciones.marketdata.sectors import ChainedSectors
from scanner_opciones.marketdata.finnhub import FinnhubNews
from scanner_opciones.marketdata.yahoo import YahooAnalysts, YahooDescriptions, YahooSectors
from scanner_opciones.marketdata.tastytrade import TastytradeVolatility, quiet_sdk_logging
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import ChainCacheRepo, MetaRepo
from scanner_opciones.tortuga.routines import RoutineRunner
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

    financials = None
    if settings.edgar.contact.strip():
        financials = EdgarFinancialsProvider(settings.edgar.contact.strip(), settings.edgar.requests_per_second)
    else:
        logging.getLogger(__name__).info(
            "Sin edgar.contact en config.yaml: no se consulta SEC EDGAR (filtros de apalancamiento y flujo de caja sin datos)")

    sector_providers = []
    for name in md.sector_sources:
        if name == "edgar" and financials is not None:
            sector_providers.append(financials)
        elif name == "yahoo":
            sector_providers.append(YahooSectors(settings.analysts.concurrency))
    sectors = ChainedSectors(sector_providers) if sector_providers else None

    calendar = MarketCalendar.from_settings(settings.market)

    def closed_since():
        """Último cierre de sesión si el mercado está cerrado ahora; None con el mercado abierto."""
        now = datetime.now().astimezone()
        return None if calendar.is_open(now) else calendar.last_close(now)

    def factory(mode: AccountMode) -> BrokerGateway:
        ibkr = IBKRGateway(settings.ibkr.model_copy(update={"mode": mode}))
        # «tastytrade»: cadena, cotizaciones, precios y ex-dividendos de tastytrade; cuenta, margen, sector y VIX de IBKR
        return (HybridGateway(ibkr, tasty, md, fallback_batch=settings.refresh.batch_size, sectors=sectors,
                              chain_store=ChainCacheRepo(db), closed_since=closed_since)
                if md.source == "tastytrade" else ibkr)

    service = AppService(factory(settings.ibkr.mode), db, settings, volatility=tasty, prices=tasty, candles=tasty,
                         fundamentals=tasty, financials=financials, names=tasty, watchlists=tasty,
                         analysts=YahooAnalysts(settings.analysts.concurrency) if settings.analysts.enabled else None,
                         descriptions=YahooDescriptions(settings.company.concurrency) if settings.company.enabled else None,
                         news=(FinnhubNews(settings.finnhub.api_token.strip(), settings.company.calls_per_minute)
                               if settings.company.enabled and settings.finnhub.api_token.strip() else None),
                         tortuga=RoutineRunner(settings, MetaRepo(db)))
    runner = PeriodicRunner(service.refresh_periodic, settings.refresh_interval_minutes * 60)
    account_runner = PeriodicRunner(
        lambda: service.refresh_account(skip_closed_vix=True), settings.refresh.account_interval_minutes * 60)
    startup_task: list[asyncio.Task] = []

    async def reconnect() -> None:
        """Reintenta la conexión con TWS mientras esté caída. Solo si algún puerto responde (sin ruido en el log);
        si cambió el puerto abierto (TWS real <-> simulada) se cambia de modo, y si no se reconecta en el actual."""
        if service.state.connected:
            return
        ib = service.settings.ibkr
        mode = await asyncio.to_thread(detect_mode, ib) if ib.auto_detect_mode else ib.mode
        port = ib.ports.live if mode is AccountMode.LIVE else ib.ports.paper
        if not await asyncio.to_thread(port_open, ib.host, port):
            return
        if mode is not ib.mode:
            service.settings = service.settings.model_copy(update={"ibkr": ib.model_copy(update={"mode": mode})})
            await service.switch_gateway(factory(mode))
        else:
            await service.reconnect()
        if service.state.connected:
            logging.getLogger(__name__).info("Conexión con TWS recuperada (%s)", mode.value)

    reconnect_runner = PeriodicRunner(reconnect, settings.ibkr.reconnect_interval_seconds)

    async def on_startup() -> None:
        # No bloquea la UI si TWS tarda o no está disponible
        startup_task.append(asyncio.create_task(service.start()))
        runner.start()
        account_runner.start()
        reconnect_runner.start()

    async def on_shutdown() -> None:
        await reconnect_runner.stop()
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
