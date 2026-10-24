"""Evaluación de la admisión a la Watchlist (ver `scanner/admission.py`): reúne los datos de cada ticker del Universo y de la Watchlist y aplica
los dos estilos (TPC y Tastytrade) y las fuentes directas. Solo mide; no añade ni quita nada."""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Any, Optional

from scanner_opciones.scanner.admission import (NO_FILTERS_NOTE, NO_OPTIONS_NOTE, NOT_APPLIED_NOTE, TREND_LABELS, AdmissionReport, SourceRule, Verdict, canon, reason_label,
                                                tasty_failure_reasons, tpc_failure_reasons, tpc_trend_reject)

log = logging.getLogger(__name__)
CLOSES_TTL_HOURS = 6        # los cierres pedidos para medir la tendencia se reutilizan (evaluar dos veces seguidas no repite la descarga)
IV_TTL_MINUTES = 30
CANDLE_BATCH = 100
DIRECT_NOTE = "fuente directa: no se evalúa"
MIN_STORED_SESSIONS = 230   # 200 sesiones de la media más las 20 de su pendiente


async def evaluate(svc: Any) -> AdmissionReport:
    """Evalúa Universo + Watchlist. `svc` es el `AppService` (se usa su estado, repositorios y proveedores). Cada fuente tiene su `SourceRule` (directa o
    qué filtros de cada estilo aplica); un ticker pasa si lo admite la regla de alguna de sus fuentes (sin fuente: todos los filtros). Con «sin filtros»
    (`svc.admission_filters_off()`) pasa todo y no se mide nada."""
    cfg = svc.settings.admission
    now: datetime = svc.now()
    watchlist = set(svc.watchlist.list())
    lists = svc.ticker_lists()
    tickers = sorted(watchlist | set(lists))
    rules = {n: svc.admission_rule(n) for n in {n for names in lists.values() for n in names}}
    held = {p.ticker.replace(" ", "-") for p in svc.state.positions}
    notes: list[str] = []

    if svc.admission_filters_off():
        notes.append("Los filtros de admisión están quitados: pasa todo el Universo.")
        verdicts = [Verdict(t, t in watchlist, tuple(lists.get(t, ())), False, NO_FILTERS_NOTE, NO_FILTERS_NOTE, protected=t in held,
                            via=("Sin filtros",), passed_sources=tuple(lists.get(t, ()))) for t in tickers]
        return AdmissionReport(now, tuple(verdicts), tuple(notes), rules, filters_off=True)

    def rules_of(t: str) -> list[SourceRule]:
        return [rules[n] for n in lists.get(t, ())] or [SourceRule()]

    # ¿tiene opciones? Casi siempre ya se comprobó al entrar en el Universo; aquí solo se pide lo que quedó sin comprobar (una petición, recordada)
    to_check = [t for t in tickers if t in lists and any(r.options for r in rules_of(t))]
    status = await svc.options_status(to_check) if to_check else {}
    if status is None:
        notes.append("No se pudo comprobar qué tickers tienen opciones: no se descarta ninguno por eso.")
        status = {}
    without = {t for t, ok in status.items() if not ok}

    def admits(t: str, r: SourceRule) -> bool:
        return not (r.options and t in without)

    direct = {t for t in tickers if t in lists and any(r.direct and admits(t, r) for r in rules_of(t))}
    todo = [t for t in tickers if t not in direct]

    qinfos, winfos, ident, exempt = svc.quality.all(), svc.ticker_info.all(), svc.universe_identity(), svc.quality_exempt()

    def info_of(t: str):
        info = winfos.get(t) or qinfos.get(t)
        if info is not None and info.sector is None and t in ident and ident[t][1]:
            info = replace(info, sector=ident[t][1])
        return info

    # TPC: primero la calidad (barata, con lo guardado) y solo a quienes les falta la tendencia por medir (la calidad que exige alguna de sus fuentes la pasan) se les mide
    tpc_cfg = cfg.tpc if svc.settings.edgar.contact.strip() else cfg.tpc.model_copy(update={"require_positive_fcf": False})
    if not svc.settings.edgar.contact.strip():
        notes.append("Sin edgar.contact no hay flujo de caja libre: el estilo TPC no lo exige.")
    md = svc.settings.scanner.quality.manageable_debt
    manageable = (md.max_debt_to_equity, md.min_interest_coverage)
    quality = {t: tpc_failure_reasons(info_of(t), tpc_cfg, exempt, manageable, None) for t in todo}      # etiqueta → motivo, sin la tendencia

    def needs_trend(t: str) -> bool:
        return tpc_cfg.require_uptrend and any("Tendencia" in r.tpc_labels and not (set(quality[t]) & (r.tpc_labels - {"Tendencia"})) for r in rules_of(t))

    candidates = [t for t in todo if needs_trend(t)]
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
    # cada filtro por separado: la tendencia se mide también en quien falló la calidad, pero solo con los cierres que ya hay guardados (no se descargan)
    stored = svc.bars.all_closes([t for t in todo if t not in candidates])
    trend: dict[str, object] = {}
    for t in todo:
        values = closes.get(t) or [px for _, px in stored.get(t, ())]
        if t in candidates or len(values) >= MIN_STORED_SESSIONS:
            trend[t] = tpc_trend_reject(values, *trend_args)
        else:
            trend[t] = None         # sin cierres suficientes guardados no se puede medir: no cuenta como rechazo

    # Tastytrade: IV Rank / Percentile (guardados en la Watchlist; del Universo se piden juntos) y liquidez de opciones
    svc.state.activity = "Admisión: IV Rank e IV Percentile del Universo"
    uses_iv = [t for t in todo if any("iv" in r.tasty for r in rules_of(t))]
    ivs = await _ivs(svc, [t for t in uses_iv if t not in watchlist or (winfos.get(t) is None or winfos[t].iv_rank is None)], now, notes)
    tpc_why: dict[str, dict] = {}
    tasty_why: dict[str, dict] = {}
    for t in todo:
        info, iv = info_of(t), ivs.get(t)
        rank, pct = (iv.iv_rank, iv.iv_percentile) if iv is not None else (None, None)
        if rank is None and pct is None and winfos.get(t) is not None:
            rank, pct = winfos[t].iv_rank, winfos[t].iv_percentile
        tpc_why[t] = dict(quality[t])
        if trend[t] is not None:
            tpc_why[t].setdefault(reason_label(str(trend[t])), str(trend[t]))
        tasty_why[t] = tasty_failure_reasons(rank, pct, info.option_liquidity if info is not None else None, cfg.tastytrade)

    def shown(why: dict, enabled: set[str], applied: bool) -> Optional[str]:
        """Motivo que se enseña: el primero de los filtros que alguna de sus fuentes aplica; None = los cumple."""
        if not applied:
            return NOT_APPLIED_NOTE
        for label, reason in why.items():
            if TREND_LABELS.get(label, label) in enabled:
                return reason
        return None

    verdicts = []
    for t in tickers:
        names = tuple(lists.get(t, ()))
        if t in direct:
            verdicts.append(Verdict(t, t in watchlist, names, True, DIRECT_NOTE, DIRECT_NOTE, protected=t in held, via=("Directa",),
                                    passed_sources=tuple(n for n in names if admits(t, rules[n])), no_options=t in without))
            continue
        tpc_fail, tasty_fail = canon(tpc_why[t]), set(tasty_why[t])
        via: dict[str, None] = {}
        passed = []
        for n in names or (None,):
            rule = rules[n] if n is not None else SourceRule()
            ok, why = rule.evaluate(tpc_fail, tasty_fail)
            if ok and admits(t, rule):
                via.update(dict.fromkeys(why))
                if n is not None:
                    passed.append(n)
        rs = rules_of(t)
        tpc_on = set().union(*(r.tpc_labels for r in rs))
        tasty_on = set().union(*(r.tasty_labels for r in rs))
        none = t in without and not passed and any(r.options for r in rs)
        verdicts.append(Verdict(t, t in watchlist, names, False, NO_OPTIONS_NOTE if none else shown(tpc_why[t], tpc_on, bool(tpc_on)),
                                NO_OPTIONS_NOTE if none else shown(tasty_why[t], tasty_on, bool(tasty_on)), protected=t in held, tpc_all=tuple(tpc_why[t]),
                                tasty_all=tuple(tasty_why[t]), via=tuple(via), passed_sources=tuple(passed), no_options=t in without))
    return AdmissionReport(now, tuple(verdicts), tuple(notes), rules)


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
