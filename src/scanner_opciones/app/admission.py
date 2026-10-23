"""Evaluación de la admisión a la Watchlist (ver `scanner/admission.py`): reúne los datos de cada ticker del Universo y de la Watchlist y aplica
los dos estilos (TPC y Tastytrade) y las fuentes directas. Solo mide; no añade ni quita nada."""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any

from scanner_opciones.scanner.admission import AdmissionReport, Verdict, tasty_failures, tasty_reject, tpc_failures, tpc_quality_reject, tpc_trend_reject

log = logging.getLogger(__name__)
CLOSES_TTL_HOURS = 6        # los cierres pedidos para medir la tendencia se reutilizan (evaluar dos veces seguidas no repite la descarga)
IV_TTL_MINUTES = 30
CANDLE_BATCH = 100
DIRECT_NOTE = "fuente directa: no se evalúa"
MIN_STORED_SESSIONS = 230   # 200 sesiones de la media más las 20 de su pendiente


async def evaluate(svc: Any) -> AdmissionReport:
    """Evalúa Universo + Watchlist. `svc` es el `AppService` (se usa su estado, repositorios y proveedores)."""
    cfg = svc.settings.admission
    now: datetime = svc.now()
    watchlist = set(svc.watchlist.list())
    lists = svc.ticker_lists()
    tickers = sorted(watchlist | set(lists))
    direct_names = svc.direct_source_names()
    direct = {t for t in tickers if any(n in direct_names for n in lists.get(t, ()))}
    todo = [t for t in tickers if t not in direct]
    notes: list[str] = []

    qinfos, winfos, ident, exempt = svc.quality.all(), svc.ticker_info.all(), svc.universe_identity(), svc.quality_exempt()

    def info_of(t: str):
        info = winfos.get(t) or qinfos.get(t)
        if info is not None and info.sector is None and t in ident and ident[t][1]:
            info = replace(info, sector=ident[t][1])
        return info

    # TPC: primero la calidad (barata, con lo guardado) y solo a los que pasan se les mide la tendencia
    tpc_cfg = cfg.tpc if svc.settings.edgar.contact.strip() else cfg.tpc.model_copy(update={"require_positive_fcf": False})
    if not svc.settings.edgar.contact.strip():
        notes.append("Sin edgar.contact no hay flujo de caja libre: el estilo TPC no lo exige.")
    md = svc.settings.scanner.quality.manageable_debt
    tpc: dict[str, object] = {t: tpc_quality_reject(info_of(t), tpc_cfg, exempt, (md.max_debt_to_equity, md.min_interest_coverage)) for t in todo}
    candidates = [t for t in todo if tpc[t] is None]
    closes = {t: [px for _, px in bars] for t, bars in svc.bars.all_closes(candidates).items()}
    lacking = [t for t in candidates if len(closes.get(t, ())) < MIN_STORED_SESSIONS]
    if lacking:
        svc.state.activity = f"Admisión: descargando cierres de {len(lacking)} candidatos"
        fetched = await _closes(svc, lacking, cfg.history_days, now, notes)
        for t, values in fetched.items():
            if len(values) > len(closes.get(t, ())):
                closes[t] = values
    spread = svc.settings.spread_universe
    trend_args = (tpc_cfg, spread.uptrend_ma, spread.uptrend_slope_days, spread.uptrend_fallback_ma)
    for t in candidates:
        tpc[t] = tpc_trend_reject(closes.get(t, []), *trend_args)
    # cada filtro por separado: la tendencia se mide también en quien falló la calidad, pero solo con los cierres que ya hay guardados (no se descargan)
    stored = svc.bars.all_closes([t for t in todo if t not in candidates])
    trend_all: dict[str, object] = {}
    for t in todo:
        values = closes.get(t) or [px for _, px in stored.get(t, ())]
        if t in candidates:
            trend_all[t] = tpc[t] if tpc[t] is None or "tendencia" in str(tpc[t]) or "histórico" in str(tpc[t]) else tpc_trend_reject(values, *trend_args)
        elif len(values) >= MIN_STORED_SESSIONS:
            trend_all[t] = tpc_trend_reject(values, *trend_args)
        else:
            trend_all[t] = None         # sin cierres suficientes guardados no se puede medir: no cuenta como rechazo

    # Tastytrade: IV Rank / Percentile (guardados en la Watchlist; del Universo se piden juntos) y liquidez de opciones
    svc.state.activity = "Admisión: IV Rank e IV Percentile del Universo"
    ivs = await _ivs(svc, [t for t in todo if t not in watchlist or (winfos.get(t) is None or winfos[t].iv_rank is None)], now, notes)
    tasty: dict[str, object] = {}
    for t in todo:
        info, iv = info_of(t), ivs.get(t)
        rank = (iv.iv_rank if iv is not None else None)
        pct = (iv.iv_percentile if iv is not None else None)
        if rank is None and pct is None and winfos.get(t) is not None:
            rank, pct = winfos[t].iv_rank, winfos[t].iv_percentile
        tasty[t] = tasty_reject(rank, pct, info.option_liquidity if info is not None else None, cfg.tastytrade)

    tpc_all = {t: tpc_failures(info_of(t), tpc_cfg, exempt, (md.max_debt_to_equity, md.min_interest_coverage), trend_all[t]) for t in todo}
    tasty_all = {}
    for t in todo:
        info, iv = info_of(t), ivs.get(t)
        rank, pct = (iv.iv_rank, iv.iv_percentile) if iv is not None else (None, None)
        if rank is None and pct is None and winfos.get(t) is not None:
            rank, pct = winfos[t].iv_rank, winfos[t].iv_percentile
        tasty_all[t] = tasty_failures(rank, pct, info.option_liquidity if info is not None else None, cfg.tastytrade)

    held = {p.ticker.replace(" ", "-") for p in svc.state.positions}
    verdicts = [Verdict(t, t in watchlist, tuple(lists.get(t, ())), t in direct,
                        DIRECT_NOTE if t in direct else tpc[t], DIRECT_NOTE if t in direct else tasty[t],
                        protected=t in held, tpc_all=tpc_all.get(t, ()), tasty_all=tasty_all.get(t, ())) for t in tickers]
    return AdmissionReport(now, tuple(verdicts), tuple(notes))


async def _closes(svc: Any, tickers: list[str], days: int, now: datetime, notes: list[str]) -> dict[str, list[float]]:
    cache: dict = svc._admission_closes
    fresh = {t for t in tickers if t in cache and (now - cache[t][0]).total_seconds() < CLOSES_TTL_HOURS * 3600}
    out = {t: cache[t][1] for t in fresh if cache[t][1]}
    pending = [t for t in tickers if t not in fresh]
    provider = getattr(svc.daily, "candles", None)
    if pending and provider is None:
        notes.append("Sin proveedor de velas: no se pudo medir la tendencia de los candidatos.")
        return out
    for i in range(0, len(pending), CANDLE_BATCH):
        batch = pending[i:i + CANDLE_BATCH]
        try:
            bars = await provider.get_daily_closes(batch, days)
        except Exception as exc:     # tastytrade corta o tarda: esos candidatos quedan «sin histórico» y se reintentan en la próxima evaluación
            log.warning("Admisión: no se pudieron descargar los cierres de %d tickers: %s", len(batch), exc)
            notes.append(f"No se pudieron descargar los cierres de {len(batch)} candidatos ({str(exc)[:80]}).")
            continue
        for t in batch:
            values = [px for _, px in bars.get(t, ())]
            cache[t] = (now, values)          # también los que no devuelven velas: no se vuelven a pedir en cada evaluación
            if values:
                out[t] = values
    return out


async def _ivs(svc: Any, tickers: list[str], now: datetime, notes: list[str]) -> dict:
    cache: dict = svc._admission_iv
    out = {t: cache[t][1] for t in tickers if t in cache and (now - cache[t][0]).total_seconds() < IV_TTL_MINUTES * 60}
    pending = [t for t in tickers if t not in out]
    if pending and svc.volatility is None:
        notes.append("Sin proveedor de IV: el estilo Tastytrade solo usa lo guardado de la Watchlist.")
        return out
    if pending:
        try:
            metrics = await svc.volatility.get_iv_metrics(pending)
        except Exception as exc:
            log.warning("Admisión: no se pudo pedir el IV Rank de %d tickers: %s", len(pending), exc)
            notes.append(f"No se pudo pedir el IV Rank del Universo ({str(exc)[:80]}).")
            return out
        for t, m in metrics.items():
            cache[t] = (now, m)
            out[t] = m
    return out
