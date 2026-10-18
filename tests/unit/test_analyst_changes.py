"""Cambios de valoración de analistas (Yahoo Finance): clasificación, lectura del DataFrame de yfinance, guardado, actualización y marca en la interfaz."""
import asyncio
from datetime import date, datetime, timedelta

import pandas as pd

from scanner_opciones.config.settings import Settings
from scanner_opciones.marketdata.analysts import AnalystChange, AnalystError, FakeAnalysts, classify_changes, summary
from scanner_opciones.marketdata.yahoo import YahooAnalysts, rows_from_frame, yahoo_symbol
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import AnalystRepo
from scanner_opciones.ui.web import analyst_flag

TODAY = date(2026, 10, 18)


def row(day, firm="JP Morgan", action="main", frm="Buy", to="Buy", pt_from=0, pt_to=0):
    return {"day": day, "firm": firm, "action": action, "from": frm, "to": to, "pt_from": pt_from, "pt_to": pt_to}


def test_changes_are_filtered_by_window_and_plain_reiterations_are_dropped():
    changes = classify_changes([
        row("2026-10-12", action="down", frm="Buy", to="Hold"),
        row("2026-10-01", action="main", pt_from=340, pt_to=360),            # reitera pero sube el objetivo: cuenta
        row("2026-10-05", action="reit", pt_from=0, pt_to=0),                 # reitera sin más: fuera
        row("2026-08-01", action="up", frm="Hold", to="Buy"),                 # fuera de la ventana
        {"day": "no-es-fecha"}, row("2026-10-17", action="init", frm="", to="Buy"),
    ], TODAY, 30)
    assert [c.day.isoformat() for c in changes] == ["2026-10-17", "2026-10-12", "2026-10-01"]     # más recientes primero
    assert changes[1].text.startswith("12/10/2026 · JP Morgan: BAJA el rating de Buy a Hold") and "objetivo sube de 340.00 $ a 360.00 $" in changes[2].text


def test_summary_prefers_downgrades_over_everything():
    down = AnalystChange(TODAY, "A", "down", "Buy", "Hold")
    up = AnalystChange(TODAY, "B", "up", "Hold", "Buy")
    cut = AnalystChange(TODAY, "C", "main", "Buy", "Buy", 400, 380)
    assert summary([up, down]) == ("▼", "an-dn", "1 bajada de rating (A)")
    assert summary([up])[0] == "▲" and summary([cut])[0] == "▽"


def test_yahoo_frame_is_read_and_symbols_are_translated():
    frame = pd.DataFrame({"Firm": ["JP Morgan"], "ToGrade": ["Overweight"], "FromGrade": ["Neutral"], "Action": ["up"],
                          "priceTargetAction": ["Raises"], "currentPriceTarget": [360.0], "priorPriceTarget": [340.0]},
                         index=pd.DatetimeIndex([datetime(2026, 10, 12, 18, 47)]))
    rows = rows_from_frame(frame)
    assert rows == [{"day": date(2026, 10, 12), "firm": "JP Morgan", "action": "up", "from": "Neutral", "to": "Overweight",
                     "pt_from": 340.0, "pt_to": 360.0}]
    assert rows_from_frame(None) == [] and rows_from_frame(pd.DataFrame()) == []
    assert yahoo_symbol("brk.b") == "BRK-B" and yahoo_symbol("PBR A") == "PBR-A"


def test_a_failing_provider_raises_after_too_many_failures_and_keeps_going_before(monkeypatch):
    import sys
    import types

    class Ticker:
        def __init__(self, symbol):
            self.symbol = symbol

        @property
        def upgrades_downgrades(self):
            if self.symbol == "BAD":
                raise RuntimeError("Too Many Requests")
            return pd.DataFrame({"Firm": ["X"], "ToGrade": ["Buy"], "FromGrade": ["Hold"], "Action": ["up"],
                                 "currentPriceTarget": [10.0], "priorPriceTarget": [9.0]}, index=pd.DatetimeIndex([datetime(2026, 10, 15)]))

    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))
    provider = YahooAnalysts(today=lambda: TODAY)
    found = asyncio.run(provider.get_analyst_changes(["AAPL", "BAD", "MSFT"], 30))
    assert set(found) == {"AAPL", "MSFT"} and found["AAPL"][0].action == "up"          # un fallo suelto no tumba la pasada
    try:
        asyncio.run(provider.get_analyst_changes(["BAD"] * 12, 30))
    except AnalystError:
        pass
    else:
        raise AssertionError("debía fallar tras varios errores seguidos")


def test_repo_roundtrip_and_purge():
    repo = AnalystRepo(Database())
    now = datetime(2026, 10, 18, 9, 0)
    repo.save({"AAPL": [AnalystChange(date(2026, 10, 12), "JP Morgan", "down", "Buy", "Hold", 340, 330)], "KO": []}, now)
    saved = repo.all()
    assert saved["AAPL"][0] == now and saved["AAPL"][1][0].target_to == 330 and saved["KO"][1] == []
    assert repo.purge_except(["AAPL"]) == 1 and set(repo.all()) == {"AAPL"}


def test_downgrades_come_first_in_the_tooltip_even_when_newer_changes_are_target_raises():
    raise_pt = AnalystChange(date(2026, 10, 15), "Goldman Sachs", "main", "Neutral", "Neutral", 120, 137)
    down = AnalystChange(date(2026, 9, 30), "Citigroup", "down", "Neutral", "Sell", 60, 80)
    joined = str(analyst_flag({"MRNA": [raise_pt, down]}, "MRNA"))
    assert joined.index("Citigroup: BAJA el rating de Neutral a Sell") < joined.index("Goldman Sachs: mantiene Neutral")
    assert "objetivo sube de 60.00 $ a 80.00 $" in joined and "1 bajada de rating (Citigroup)" in joined


def test_flag_shows_the_detail_on_hover_and_nothing_without_changes():
    change = AnalystChange(date(2026, 10, 12), "JP Morgan", "down", "Buy", "Hold")
    html = str(analyst_flag({"AAPL": [change]}, "AAPL"))
    assert "▼" in html and "an-dn" in html and "JP Morgan: BAJA el rating de Buy a Hold" in html and "1 bajada de rating (JP Morgan)" in html
    assert analyst_flag({"AAPL": [change]}, "MSFT") == "" and analyst_flag(None, "AAPL") == ""


async def test_daily_updater_queries_only_stale_tickers_and_keeps_what_it_has_when_yahoo_fails():
    from scanner_opciones.broker.fake_gateway import FakeGateway
    from scanner_opciones.app.service import AppService

    now = [datetime(2026, 10, 18, 9, 0)]
    fake = FakeAnalysts({"AAPL": [AnalystChange(date(2026, 10, 12), "JP Morgan", "down", "Buy", "Hold")]})
    svc = AppService(FakeGateway(), Database(), Settings(), lambda: now[0], analysts=fake)
    assert await svc.daily.update_analysts(["AAPL", "KO"]) == 2
    assert [a.firm for a in svc.analyst_map()["AAPL"]] == ["JP Morgan"] and "KO" not in svc.analyst_map()
    assert await svc.daily.update_analysts(["AAPL", "KO"]) == 0                     # recién consultados: nada
    assert len(fake.calls) == 1
    now[0] += timedelta(hours=21)
    fake.error = AnalystError("limitado")
    assert await svc.daily.update_analysts(["AAPL", "KO"]) == 0                     # Yahoo falla: se conservan los guardados
    assert "AAPL" in svc.analyst_map()
    now[0] += timedelta(days=31)
    assert svc.analyst_map() == {}                                                  # fuera de la ventana: ya no se marca


def test_ticker_tooltip_shows_the_full_company_name_first():
    from scanner_opciones.ui.web import tk_title
    names = {"BRKB": "Berkshire Hathaway Inc. Class B", "QQQ": "Invesco QQQ Trust"}
    assert tk_title(names, "BRK-B", "Ver el historial") == "Berkshire Hathaway Inc. Class B\nVer el historial"
    assert tk_title(names, "QQQ") == "Invesco QQQ Trust" and tk_title(names, "ZZZ", "solo esto") == "solo esto" and tk_title(None, "QQQ") == ""
