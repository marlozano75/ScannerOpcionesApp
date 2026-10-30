"""Registro del escaneo automático de perfiles: apariciones, desapariciones con motivo, mercados cerrados y volcado a Google Sheets."""
from datetime import date, datetime
from types import SimpleNamespace as NS

from scanner_opciones.config.settings import ScanLogSettings, SheetsSettings
from scanner_opciones.jobs.scan_log import ScanLogJob
from scanner_opciones.jobs.scan_log_sheets import ACTIVE_TAB, EVENTS_TAB, RUNS_TAB, SheetsWriter
from scanner_opciones.scanner.engine import ScanOutput
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.scan_log import ScanLogRepo


class Crit:
    def with_filters(self, **kw):
        return self


def result(ticker, strike, expiry=date(2026, 11, 20)):
    return NS(snapshot=NS(contract=NS(ticker=ticker, expiry=expiry, strike=strike)), reference_price=1.5, net_price=1.5, yield_ref_annualized_pct=30.0, dte=22,
              strike_distance_pct=12.0)


class World:
    """Estado mutable que el escaneo simulado devuelve y el reloj/los mercados que consulta el job."""
    def __init__(self):
        self.results, self.rejections, self.open = [], {}, {"AAA": True, "SAP": True}
        self.t = datetime(2026, 11, 2, 10, 0)

    def scan(self, crit, rejections):
        return ScanOutput(self.results, 0, dict(self.rejections), [])


def make(world, profiles=(), writer=None):
    repo = ScanLogRepo(Database(":memory:"))
    job = ScanLogJob(repo, ScanLogSettings(enabled=True, profiles=list(profiles)), world.scan, lambda: [("Tasty", Crit(), None), ("Otro", Crit(), None)],
                     lambda t: world.open.get(t, True), lambda: world.t, writer)
    return repo, job


def events(repo):
    return [(e["event"], e["profile"], e["ticker"], e["reason"]) for e in repo.db.conn.execute("SELECT * FROM scan_events ORDER BY id")]


def test_aparece_y_desaparece_con_motivo():
    w = World()
    repo, job = make(w, ["Tasty"])
    w.results = [result("AAA", 100.0)]
    job.scan_all()
    job.scan_all()                        # sigue: no repite el evento
    assert events(repo) == [("aparece", "Tasty", "AAA", None)]
    w.results = []
    w.rejections = {"AAA 2026-11-20 100.0": "yield 12,0 % < mínimo 20 %"}
    job.scan_all()
    assert events(repo)[-1] == ("desaparece", "Tasty", "AAA", "yield 12,0 % < mínimo 20 %")
    assert repo.all_active() == []
    w.results = [result("AAA", 100.0)]    # vuelve: episodio nuevo
    job.scan_all()
    assert [e[0] for e in events(repo)] == ["aparece", "desaparece", "aparece"]


def test_motivos_sin_rechazo():
    w = World()
    repo, job = make(w, ["Tasty"])
    w.results = [result("AAA", 100.0, date(2026, 11, 2))]
    job.scan_all()
    w.t = datetime(2026, 11, 3, 10, 0)
    w.results = []
    job.scan_all()
    assert events(repo)[-1][3] == "vencido"


def test_mercado_cerrado_no_cierra_ni_abre():
    w = World()
    repo, job = make(w, ["Tasty"])
    w.results = [result("SAP", 150.0), result("AAA", 100.0)]
    job.scan_all()
    w.open["SAP"] = False
    w.results = [result("AAA", 100.0)]          # SAP sin cotizaciones nuevas: no cuenta como desaparecido
    job.scan_all()
    assert [e for e in events(repo) if e[0] == "desaparece"] == []
    assert {r.ticker for r in repo.all_active()} == {"SAP", "AAA"}
    run = repo.db.conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    assert run["skipped_tickers"] == 1
    w.open["SAP"] = True                        # reabre y ya no sale: ahora sí desaparece
    job.scan_all()
    assert ("desaparece", "Tasty", "SAP") == events(repo)[-1][:3]


def test_solo_los_perfiles_elegidos_y_todos_si_vacio():
    w = World()
    w.results = [result("AAA", 100.0)]
    repo, job = make(w, ["Otro"])
    job.scan_all()
    assert {e[1] for e in events(repo)} == {"Otro"}
    repo, job = make(w, [])
    job.scan_all()
    assert {e[1] for e in events(repo)} == {"Tasty", "Otro"}


class FakeWs:
    def __init__(self):
        self.rows, self.cleared = [], 0
    def append_rows(self, rows, value_input_option=None):
        self.rows += rows
    def clear(self):
        self.cleared += 1
    def update(self, values, rng):
        self.rows = list(values)


class FakeSheet:
    def __init__(self):
        self.tabs = {}
    def worksheet(self, title):
        if title not in self.tabs:
            raise KeyError(title)
        return self.tabs[title]
    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = FakeWs()
        return self.tabs[title]


async def test_volcado_a_sheets_y_reintento():
    w = World()
    sheet, fail = FakeSheet(), [True]

    def open_sheet(cfg):
        if fail[0]:
            raise RuntimeError("sin red")
        return sheet

    repo = ScanLogRepo(Database(":memory:"))
    writer = SheetsWriter(SheetsSettings(enabled=True, spreadsheet_id="x"), repo, open_sheet)
    job = ScanLogJob(repo, ScanLogSettings(enabled=True), w.scan, lambda: [("Tasty", Crit(), None)], lambda t: True, lambda: w.t, writer)
    w.results = [result("AAA", 100.0)]
    await job.run()
    assert writer.last_error and sheet.tabs == {}      # falló: nada se pierde
    fail[0] = False
    await job.run()
    assert writer.last_error is None
    assert len(sheet.tabs[EVENTS_TAB].rows) == 2 and sheet.tabs[EVENTS_TAB].rows[1][1] == "aparece"     # cabecera + 1 evento
    assert len(sheet.tabs[RUNS_TAB].rows) == 3                                                         # cabecera + 2 pasadas
    assert len(sheet.tabs[ACTIVE_TAB].rows) == 2       # cabecera + 1 activo
    await job.run()                                     # sin novedades: no duplica eventos
    assert len(sheet.tabs[EVENTS_TAB].rows) == 2


def test_oauth_sin_token_no_abre_el_navegador(tmp_path):
    import pytest
    from scanner_opciones.jobs.scan_log_sheets import _open_spreadsheet
    pytest.importorskip("gspread")
    cfg = SheetsSettings(enabled=True, spreadsheet_id="x", auth="oauth", oauth_token_file=str(tmp_path / "no-existe.json"))
    with pytest.raises(RuntimeError, match="falta autorizar"):
        _open_spreadsheet(cfg)
