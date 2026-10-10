"""Ayudas de los tests de calidad: qué tickers del Universo pasan unos filtros, con el MISMO parser del formulario
(`read_quality`) y la MISMA regla (`ticker_quality_reject`) que usa el Scanner."""
from dataclasses import replace

from starlette.datastructures import QueryParams

from scanner_opciones.domain.models import TickerInfo
from scanner_opciones.scanner.quality import ticker_quality_reject
from scanner_opciones.ui.web import quality_form, read_quality


def passing(svc, query: str) -> set[str]:
    """Tickers del Universo que pasan los filtros de calidad de `query` (p. ej. «q_profit=on&q_de=strict»).
    Lanza `ValueError` si algún valor del formulario no es válido, como hace el Scanner."""
    qcfg, base = svc.settings.scanner.quality, svc.criteria()
    form, overrides = quality_form(base), {}
    read_quality(QueryParams(query), form, overrides, qcfg, earnings=False, solvency=True)
    criteria = base.with_filters(**overrides)
    ident, infos = svc.universe_identity(), svc.quality.all()
    out = set()
    for src in svc.universe_sources:
        for row in src.table.rows:
            info = replace(infos.get(row.ticker) or TickerInfo(row.ticker), sector=ident.get(row.ticker, ("", ""))[1] or None)
            if ticker_quality_reject(info, criteria, qcfg.exempt_sectors) is None:
                out.add(row.ticker)
    return out
