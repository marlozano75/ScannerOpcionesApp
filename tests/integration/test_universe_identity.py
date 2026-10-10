"""Empresa y sector de cada ticker del Universo: salen de las fuentes y, para las manuales, se completan con
tastytrade (nombre) e IBKR (sector)."""
from datetime import datetime

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.errors import VolatilityError
from scanner_opciones.storage.db import Database
from scanner_opciones.universe.sources import load_sources
from scanner_opciones.watchlist.parser import parse_text
from tests.integration.test_web import HELLO_NAME, LOWER, client_and_service, hello_xlsx, load_hello  # noqa: F401

NOW = datetime(2026, 10, 10, 12, 0)


class FakeNames:
    def __init__(self, names=None, fail=False):
        self.names, self.fail, self.calls = names or {}, fail, []

    async def get_company_names(self, tickers):
        self.calls.append(list(tickers))
        if self.fail:
            raise VolatilityError("tastytrade caído")
        return {t: self.names[t] for t in tickers if t in self.names}


def make(tmp_path, names=None, gw=None):
    db = Database(tmp_path / "app.db")
    gw = gw or FakeGateway()
    gw.connected = True
    return AppService(gw, db, Settings(), lambda: NOW, names=names), db, gw


def load_hello_into(svc, tmp_path):
    path = tmp_path / "up.xlsx"
    content = hello_xlsx()
    path.write_bytes(content)
    svc.set_universe_file(HELLO_NAME, load_sources(path, HELLO_NAME), content)


async def test_a_manual_ticker_takes_company_and_sector_from_the_other_lists_without_asking_anyone(tmp_path):
    names = FakeNames({"ADBE": "ADOBE"})
    svc, _, _ = make(tmp_path, names)
    load_hello_into(svc, tmp_path)
    await svc.add_manual_tickers(parse_text("ADBE"), "Mis ideas")          # ADBE ya está en HelloStocks
    company, sector = svc.universe_identity()["ADBE"]
    assert company == "Adobe Inc" and sector == "Technology" and names.calls == []


async def test_an_unknown_manual_ticker_gets_the_name_from_tastytrade_and_the_sector_from_ibkr(tmp_path):
    gw = FakeGateway()
    gw.sectors["ZZZZ"] = ("Industrials", "Machinery")
    svc, db, _ = make(tmp_path, FakeNames({"ZZZZ": "ZZZ CORP"}), gw)
    svc._has_options["ZZZZ"] = True
    res = await svc.add_manual_tickers(parse_text("ZZZZ"), "Mis ideas")
    assert res["added"] == ["ZZZZ"]
    assert svc.universe_identity()["ZZZZ"] == ("ZZZ CORP", "Industrials")
    again = AppService(FakeGateway(), db, Settings(), lambda: NOW)                  # reinicio: se recuerda
    assert again.universe_identity()["ZZZZ"] == ("ZZZ CORP", "Industrials")


async def test_when_the_providers_fail_the_ticker_is_added_anyway_with_blank_company_and_sector(tmp_path):
    gw = FakeGateway()
    gw.connected = False                                                            # IBKR sin conexión
    svc, _, _ = make(tmp_path, FakeNames(fail=True), gw)
    gw.connected = False
    res = await svc.add_manual_tickers(parse_text("QQQQ"), "Mis ideas")
    assert res["added"] == ["QQQQ"] and svc.universe_identity()["QQQQ"] == ("", "")


async def test_replacing_a_manual_source_swaps_its_tickers_and_every_load_goes_to_the_history(tmp_path):
    svc, db, _ = make(tmp_path)
    load_hello_into(svc, tmp_path)
    await svc.add_manual_tickers(parse_text("AAA, BBB"), "Mis ideas")
    res = await svc.add_manual_tickers(parse_text("BBB, CCC"), "Mis ideas", replace_source=True)
    assert svc.manual_sources["Mis ideas"] == ["BBB", "CCC"]
    assert res["removed"] == ["AAA"] and res["added"] == ["BBB", "CCC"]
    assert [(h["name"], h["kind"], h["action"]) for h in svc.universe_history] == [
        ("Mis ideas", "manual", "sustituido"), ("Mis ideas", "manual", "añadido"), (HELLO_NAME, "fichero", "cargado")]
    assert AppService(FakeGateway(), db, Settings(), lambda: NOW).universe_history[0]["at"].startswith("2026-10-10")


async def test_replacing_with_no_valid_ticker_keeps_the_source(tmp_path):
    import pytest
    from scanner_opciones.domain.errors import WatchlistError
    svc, _, _ = make(tmp_path)
    await svc.add_manual_tickers(parse_text("AAA"), "Mis ideas")
    with pytest.raises(WatchlistError):
        await svc.add_manual_tickers(parse_text("1234"), "Mis ideas", replace_source=True)
    assert svc.manual_sources["Mis ideas"] == ["AAA"]


def test_the_history_tab_lists_loads_with_name_and_date(client_and_service):
    client = client_and_service[0]
    load_hello(client)
    page = client.get("/universe?view=history").text
    assert "Historial de carga" in page and HELLO_NAME in page and "fichero" in page


def test_the_identity_columns_come_first_and_are_not_repeated(client_and_service):
    client = client_and_service[0]
    load_hello(client)
    page = client.get(f"/universe?src={LOWER}").text
    header = page[page.index('id="universe-table"'):]
    header = header[:header.index("</tr>")]
    order = [h for h in ("En watchlist", "Fuentes (nº)", "Ticker", "Empresa", "Sector", "EPS 12 m", "ROE") if f"<th>{h}</th>" in header]
    assert order == ["En watchlist", "Fuentes (nº)", "Ticker", "Empresa", "Sector", "EPS 12 m", "ROE"]
    assert [header.index(f"<th>{h}</th>") for h in order] == sorted(header.index(f"<th>{h}</th>") for h in order)
    assert header.count("<th>Sector</th>") == 1 and "<th>Company</th>" not in header and "<th>Ticker</th>" in header
    assert "<td>Adobe Inc</td>" in page and "<td>Technology</td>" in page
