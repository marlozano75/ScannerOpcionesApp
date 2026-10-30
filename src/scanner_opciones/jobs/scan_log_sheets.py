"""Volcado del registro de escaneos a un Google Sheet (gspread + cuenta de servicio). Solo escribe en tu hoja; si Google falla, lo pendiente queda
marcado sin sincronizar en SQLite y se reintenta en la pasada siguiente."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from scanner_opciones.config.settings import SheetsSettings
from scanner_opciones.storage.scan_log import ScanLogRepo

log = logging.getLogger(__name__)

CHUNK = 2000   # filas por petición de escritura
EVENTS_TAB, ACTIVE_TAB, RUNS_TAB = "Eventos", "Activos", "Escaneos"
EVENTS_HEADER = ["Fecha y hora", "Evento", "Perfil", "Tipo", "Ticker", "Vencimiento", "Strike", "Strike comprado", "Motivo", "Precio", "Yield anual %",
                 "DTE", "Descuento %"]
ACTIVE_HEADER = ["Perfil", "Tipo", "Ticker", "Vencimiento", "Strike", "Strike comprado", "Primera vez", "Última vez", "Precio al aparecer",
                 "Yield al aparecer %", "Precio", "Yield anual %", "DTE", "Descuento %"]
RUNS_HEADER = ["Fecha y hora", "Perfil", "Tipo", "Cumplen", "Nuevos", "Desaparecen", "Tickers con mercado cerrado"]


def _num(v: Optional[float], digits: int = 2):
    return "" if v is None else round(v, digits)


def _txt(v) -> str:
    return "" if v is None else str(v)


def event_row(e) -> list:
    return [e["at"], e["event"], e["profile"], e["kind"], e["ticker"], e["expiry"], e["strike"], _txt(e["long_strike"]), _txt(e["reason"]),
            _num(e["price"]), _num(e["yield_ann"]), _txt(e["dte"]), _num(e["distance_pct"])]


def active_row(r) -> list:
    return [r.profile, r.kind, r.ticker, r.expiry, r.strike, _txt(r.long_strike), r.first_seen, r.last_seen, _num(r.first_price), _num(r.first_yield),
            _num(r.price), _num(r.yield_ann), _txt(r.dte), _num(r.distance_pct)]


def run_row(r) -> list:
    return [r["at"], r["profile"], r["kind"], r["found"], r["appeared"], r["vanished"], r["skipped_tickers"]]


def _open_spreadsheet(cfg: SheetsSettings, interactive: bool = False):
    import gspread      # import perezoso: solo hace falta con el volcado activado
    if cfg.auth == "oauth":
        if not interactive and not Path(cfg.oauth_token_file).exists():     # el navegador solo se abre desde la autorización manual, nunca en el ciclo
            raise RuntimeError(f"falta autorizar tu cuenta de Google: ejecuta `python -m scanner_opciones.jobs.scan_log_sheets` ({cfg.oauth_token_file})")
        client = gspread.oauth(credentials_filename=cfg.oauth_client_file, authorized_user_filename=cfg.oauth_token_file)
    else:
        client = gspread.service_account(filename=cfg.credentials_file)
    return client.open_by_key(cfg.spreadsheet_id)


class SheetsWriter:
    def __init__(self, cfg: SheetsSettings, repo: ScanLogRepo, open_spreadsheet: Callable = _open_spreadsheet) -> None:
        self.cfg = cfg
        self.repo = repo
        self._open = open_spreadsheet
        self.last_error: Optional[str] = None

    def sync(self) -> bool:
        """Sube eventos y pasadas nuevos y reescribe «Activos». Bloqueante (red): llamar desde un hilo. False si falló (se reintenta luego)."""
        try:
            sheet = self._open(self.cfg)
            events, runs = self.repo.unsynced_events(), self.repo.unsynced_runs()
            self._append(sheet, EVENTS_TAB, EVENTS_HEADER, [event_row(e) for e in events])
            self.repo.mark_synced(events=[e["id"] for e in events])
            self._append(sheet, RUNS_TAB, RUNS_HEADER, [run_row(r) for r in runs])
            self.repo.mark_synced(runs=[r["id"] for r in runs])
            ws = self._tab(sheet, ACTIVE_TAB, ACTIVE_HEADER)
            ws.clear()
            ws.update([ACTIVE_HEADER, *[active_row(r) for r in self.repo.all_active()]], "A1")
            self.last_error = None
            return True
        except Exception as exc:  # noqa: BLE001 - Google puede fallar de muchas formas; nunca debe tumbar el escaneo
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Google Sheets: no se pudo sincronizar el registro de escaneos (%s); se reintentará", self.last_error)
            return False

    @staticmethod
    def _tab(sheet, title: str, header: list[str]):
        try:
            ws = sheet.worksheet(title)
        except Exception:  # noqa: BLE001 - gspread lanza WorksheetNotFound
            ws = sheet.add_worksheet(title=title, rows=1000, cols=len(header))
            ws.update([header], "A1")
        return ws

    def _append(self, sheet, title: str, header: list[str], rows: list[list]) -> None:
        ws = self._tab(sheet, title, header)
        for i in range(0, len(rows), CHUNK):
            ws.append_rows(rows[i:i + CHUNK], value_input_option="USER_ENTERED")


def authorize(config_path: str) -> int:
    """Autorización única de OAuth: abre el navegador, guarda el token y comprueba que se ve la hoja."""
    from scanner_opciones.config.settings import load_settings
    cfg = load_settings(config_path).scan_log.sheets
    if not cfg.spreadsheet_id:
        print("Falta scan_log.sheets.spreadsheet_id en la configuración.")
        return 1
    sheet = _open_spreadsheet(cfg, interactive=True)
    print(f"Conectado a «{sheet.title}». Ya puedes activar scan_log.sheets.enabled.")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(authorize(sys.argv[1] if len(sys.argv) > 1 else "config/config.yaml"))
