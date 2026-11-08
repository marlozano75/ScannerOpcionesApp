"""Pestaña «Backtest»: todo lo probado con sus resultados (`backtest/resultados.json`, ver `docs/backtest.md`). Solo lee y muestra; no depende de TWS ni de la base."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse

from scanner_opciones.backtest import catalog


def register(app, service, render) -> None:
    """Registra las rutas en `app` (comparten `service` y `render` de `create_app`)."""
    @app.get("/backtest", response_class=HTMLResponse)
    async def backtest(request: Request, objetivo: str = ""):
        try:
            data, error = catalog.load(), ""
        except (catalog.CatalogError, OSError) as exc:
            data, error = None, str(exc)
        if objetivo not in catalog.OBJECTIVES:
            objetivo = ""
        rounds = [r for r in data.rounds if not objetivo or r.objective == objetivo] if data else []
        return render(request, "backtest.html", no_autorefresh=True, data=data, error=error, rounds=rounds, objetivo=objetivo,
                      objectives=catalog.OBJECTIVES, verdicts=catalog.VERDICTS, counts=data.counts() if data else {},
                      first_number=catalog.first_number, cell_html=catalog.cell_html)
