"""Ruta del screener «MTO CDS OTM» (Call Debit Spread muy OTM; ver docs/mto-spreads.md)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

MAX_ROWS = 300


def register(app, service, render) -> None:
    @app.get("/cds", response_class=HTMLResponse)
    async def cds(request: Request):
        cfg = service.settings.scanner.cds
        rows = service.scan_cds() if cfg.enabled else []
        return render(request, "cds.html", no_autorefresh=True, watch_data=True, cfg=cfg, rows=rows[:MAX_ROWS], total=len(rows))
