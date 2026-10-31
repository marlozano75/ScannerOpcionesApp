"""La comunidad de Tortuga en la aplicación: fuente del Universo, columna del Scanner y aviso de datos viejos."""
import json
import os
from datetime import datetime, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import CommunitySettings, Settings
from scanner_opciones.storage.db import Database
from tests.integration.test_web import BASE, client_and_service, refresh  # noqa: F401

NOW = datetime(2026, 10, 12, 14, 0)


def payload(generated="2026-10-12T13:00:00", tickers=None):
    tickers = tickers if tickers is not None else [
        {"ticker": "PEP", "month_traders": 17, "month_operations": 45, "week_traders": 6},
        {"ticker": "ASTS", "month_traders": 48, "month_operations": 238, "week_traders": 20},
        {"ticker": "RARE", "month_traders": 2, "month_operations": 3},                  # menos de 3 traders: no entra en la fuente
        {"ticker": "MCD", "month_traders": 14, "month_operations": 38},
    ]
    return json.dumps({"version": 1, "generated_at": generated, "tickers": tickers, "ideas": []})


def write(path, text, bump=0):
    path.write_text(text, encoding="utf-8")
    if bump:     # el servicio relee el fichero solo si cambia su fecha de modificación
        stamp = path.stat().st_mtime + bump
        os.utime(path, (stamp, stamp))


def make(tmp_path, **community):
    settings = Settings.model_validate({"community": {"path": str(tmp_path / "tortuga.json"), **community}})
    gw = FakeGateway()
    gw.connected = True
    svc = AppService(gw, Database(tmp_path / "app.db"), settings, lambda: NOW)
    for t in ("PEP", "ASTS", "MCD", "RARE"):
        svc._has_options[t] = True
    return svc


async def test_without_a_file_there_is_no_community_and_nothing_breaks(tmp_path):
    svc = make(tmp_path)
    assert svc.community() is None and svc.community_counts() == {} and svc.community_stale() is None
    assert await svc.sync_community_source() is None and "TortugaTrades_Dia" not in svc.manual_sources


async def test_the_top_tickers_become_a_universe_source_only_when_the_file_changes(tmp_path):
    svc = make(tmp_path, universe_top=2)
    write(tmp_path / "tortuga.json", payload())
    res = await svc.sync_community_source()
    assert svc.manual_sources["TortugaTrades_Dia"] == ["ASTS", "PEP"] and res["added"] == ["ASTS", "PEP"]        # por traders; RARE no llega a 3
    assert await svc.sync_community_source() is None                                                    # mismo JSON: no se repite
    write(tmp_path / "tortuga.json", payload("2026-10-13T13:00:00", [{"ticker": "MCD", "month_traders": 14, "month_operations": 38}]), bump=60)
    again = await svc.sync_community_source()
    assert svc.manual_sources["TortugaTrades_Dia"] == ["MCD"] and again["removed"] == ["ASTS", "PEP"]            # la fuente se sustituye entera


async def test_a_broken_file_keeps_the_last_good_data(tmp_path):
    svc = make(tmp_path)
    path = tmp_path / "tortuga.json"
    write(path, payload())
    assert svc.community_counts()["PEP"] == (17, 6)
    write(path, "{ roto", bump=60)
    assert svc.community_counts()["PEP"] == (17, 6)


async def test_stale_data_is_reported_after_the_configured_hours(tmp_path):
    svc = make(tmp_path, stale_hours=24)
    path = tmp_path / "tortuga.json"
    write(path, payload("2026-10-12T13:00:00"))
    assert svc.community_stale() is None
    write(path, payload((NOW - timedelta(days=3)).isoformat()), bump=60)
    assert round(svc.community_stale()) == 72


def test_the_scanner_shows_how_many_traders_sell_puts_on_each_ticker(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    assert "<th title=\"Traders de la comunidad" not in client.get(BASE).text           # la columna se retiró del Scanner
    path = tmp_path / "tortuga.json"
    write(path, payload(tickers=[{"ticker": "AAPL", "month_traders": 9, "month_operations": 12, "week_traders": 3}]))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    assert "<td data-sort=\"9\">9 (3)</td>" not in client.get(BASE).text


def test_the_scanner_warns_when_the_daily_routine_has_not_run(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    path = tmp_path / "tortuga.json"
    write(path, payload((svc.now() - timedelta(days=5)).isoformat()))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    assert "Los datos de la comunidad de Tortuga tienen 5 días" in client.get(BASE).text
    write(path, payload(svc.now().isoformat()), bump=60)
    assert "Los datos de la comunidad de Tortuga" not in client.get(BASE).text


def ideas_payload(contract):
    ideas = [
        {"date": "2026-10-09", "trader": "Ana", "ticker": "AAPL", "strategy": "Short Put", "expiry": contract.expiry.isoformat(),
         "short_strike": contract.strike, "long_strike": None, "contracts": 1, "credit": 1.0},
        {"date": "2026-10-09", "trader": "Beto", "ticker": "ZZZ", "strategy": "Short Put", "expiry": "2026-11-20",
         "short_strike": 30, "long_strike": None, "contracts": 1, "credit": 0.5},
    ]
    return json.dumps({"version": 1, "generated_at": "2026-10-12T13:00:00", "tickers": [{"ticker": "AAPL", "month_traders": 3, "month_operations": 4}],
                       "ideas": ideas})


def test_the_ideas_page_values_each_idea_with_todays_quote_and_filters(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    assert "Todavía no hay datos de la comunidad" in client.get("/ideas").text
    refresh(client)
    contract = svc.contracts.list("AAPL")[0]
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    page = client.get("/ideas").text
    assert '<a href="/ideas" class=active>Comunidad</a>' in page.replace('class="active"', "class=active") and "Comunidad: ideas de otros traders" in page
    assert "Vigente" in page and "Sin valorar: sin cotización de esas patas (no está en tu watchlist)" in page and "2 ideas · 1 vigentes" in page
    assert 'class="chart-link" data-ticker="AAPL"' in page and 'id="strike-dialog"' in page   # el ticker abre el gráfico del strike
    ana = client.get("/ideas?trader=Ana").text
    assert '>Ana<small>' in ana and '>Beto<small>' not in ana
    assert "Ninguna idea cumple los filtros" in client.get("/ideas?tipo=spread").text


class LiveProvider:
    """Cadena, cotizaciones, precios y cierres en memoria para un ticker fuera de la watchlist."""
    async def get_put_listing(self, ticker):
        from datetime import date
        return {(date(2026, 11, 20), 30.0): "ZZZ  261120P00030000"} if ticker == "ZZZ" else {}

    async def get_option_quotes(self, symbols):
        from scanner_opciones.domain.models import OptionQuote
        return {s: OptionQuote(bid=0.5, ask=0.6, delta=-0.12) for s in symbols}

    async def get_prices(self, tickers):
        return {t: 40.0 for t in tickers}

    async def get_daily_closes(self, tickers, days):
        from datetime import date
        return {t: [(date(2026, 10, 9), 40.0)] for t in tickers}

    async def get_iv_metrics(self, tickers):
        from scanner_opciones.marketdata.volatility import IVMetrics
        return {t: IVMetrics(iv_rank=62.0, iv_percentile=70.0) for t in tickers}


def test_ideas_outside_the_watchlist_are_valued_with_live_quotes(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(svc.contracts.list("AAPL")[0]))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    svc.volatility = LiveProvider()
    page = client.get("/ideas").text
    assert "Sin valorar: sin cotización de esas patas (no está en tu watchlist)" not in page
    ideas = {v.idea.ticker: v for v in svc.community_ideas()}
    assert ideas["ZZZ"].premium_now is not None and ideas["ZZZ"].delta_now == -0.12 and ideas["ZZZ"].price_now == 40.0
    assert ideas["ZZZ"].iv_rank == 62.0 and ideas["ZZZ"].iv_percentile == 70.0


def test_the_old_daily_source_is_renamed_on_startup(tmp_path):
    svc = make(tmp_path)
    svc.manual_sources["Tortuga"] = ["PEP"]
    svc._save_manual()
    again = AppService(FakeGateway(), Database(tmp_path / "app.db"), svc.settings, lambda: NOW)
    assert again.manual_sources == {"TortugaTrades_Dia": ["PEP"]}


def test_ideas_marked_in_the_page_feed_the_permanent_source_with_trader_and_best_strategy(client_and_service, tmp_path):
    from scanner_opciones.community.ideas import idea_key
    client, svc, gw, _ = client_and_service
    refresh(client)
    contract = svc.contracts.list("AAPL")[0]
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    views = {v.idea.ticker: v for v in svc.community_ideas()}
    key = idea_key(views["AAPL"].idea)
    assert f'value="{key}"' in client.get("/ideas").text
    page = client.post("/ideas/perm", data={"idea": [key]}, follow_redirects=True).text
    assert "Añadidos a TortugaTrades_Perm" in page and ">TTP<" in page
    assert svc.manual_sources["TortugaTrades_Perm"] == ["AAPL"]
    best = max((f for f in views["AAPL"].fits if f is not None), key=lambda f: (f.passed, f.passed / f.total))
    perm = svc.community_perm()["AAPL"]
    assert perm["traders"] == ["Ana"] and perm["strategy"] == best.name and (perm["passed"], perm["total"]) == (best.passed, best.total)
    src = next(s for s in svc.universe_sources if s.name == "TortugaTrades_Perm")
    assert [c.name for c in src.table.columns][-3:] == ["Trader", "Estrategia", "Filtros"]
    again = client.post("/ideas/perm", data={"idea": [key]}, follow_redirects=True).text      # repetir no duplica
    assert "Ya estaban" in again and svc.manual_sources["TortugaTrades_Perm"] == ["AAPL"]
    assert "No hay ninguna idea marcada" in client.post("/ideas/perm", data={}, follow_redirects=True).text


def test_fits_compare_with_the_filters_saved_in_the_profiles(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    client.get("/scanner?reset=1")                       # arranca la interfaz: registra los perfiles
    names = [s[0] for s in svc.profile_specs()]
    assert names and svc.fit_names() == names and "Tortuga" not in names
    contract = svc.contracts.list("AAPL")[0]
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    view = next(v for v in svc.community_ideas() if v.idea.ticker == "AAPL")
    assert [f.name for f in view.fits] == names      # un perfil sin ese instrumento se compara con el otro juego (TastySpread con una put)
    spread_only = next(f for f in view.fits if f.name == "TastySpread")
    assert next(c for c in spread_only.criteria if c.label == "Yield").ok is None and "no comparable" in spread_only.criteria[2].text
    spec = next(s for s in svc.profile_specs() if s[1] is not None)
    fit = next(f for f in view.fits if f and f.name == spec[0])
    assert f"mín. {spec[1].strike_below_pct_min:g} %" in next(c.text for c in fit.criteria if c.label == "Descuento")


# ---- fuentes de contratos (`feeds/`) ------------------------------------------------------------------------------------
class StaticFeed:
    """Segunda fuente de contratos de prueba: cumple el puerto `ContractFeed` sin tocar el resto de la aplicación."""
    name = "Otra"

    def __init__(self, contracts, stamp="v1"):
        self._contracts, self._stamp = contracts, stamp

    def contracts(self):
        return list(self._contracts)

    def tickers(self):
        return sorted({c.ticker for c in self._contracts})

    def stamp(self):
        return self._stamp


async def test_the_community_is_a_contract_feed_that_marks_the_source_and_the_trader(tmp_path):
    from scanner_opciones.feeds.base import ContractFeed
    svc = make(tmp_path)
    assert isinstance(svc.community_feed, ContractFeed) and list(svc.feeds) == ["Comunidad"]
    assert svc.feed_contracts() == [] and svc.feed_stamp() is None                          # sin fichero: sin contratos
    text = json.dumps({"version": 1, "generated_at": "2026-10-12T13:00:00", "tickers": [],
                       "ideas": [{"date": "2026-10-09", "trader": "Ana", "ticker": "PEP", "strategy": "Short Put", "expiry": "2026-11-20",
                                  "short_strike": 150, "long_strike": None, "contracts": 1, "credit": 1.5}]})
    write(tmp_path / "tortuga.json", text)
    [c] = svc.feed_contracts()
    assert (c.source, c.trader, c.ticker) == ("Comunidad", "Ana", "PEP") and svc.community_feed.tickers() == ["PEP"]
    assert svc.feed_stamp() == "2026-10-12T13:00:00"                                      # con una sola fuente, su marca tal cual (la caché en directo sigue valiendo)


def test_another_feed_is_added_by_registering_it_and_its_contracts_are_valued_like_the_others(client_and_service, tmp_path):
    from datetime import date
    from scanner_opciones.feeds.base import ContractIdea
    client, svc, gw, _ = client_and_service
    refresh(client)
    contract = svc.contracts.list("AAPL")[0]
    write(tmp_path / "tortuga.json", ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(tmp_path / "tortuga.json")))
    mine = ContractIdea(date(2026, 10, 9), None, "AAPL", "Short Put", contract.expiry, contract.strike, None, 1, 1.0, None, source="Otra")
    svc.feeds["Otra"] = StaticFeed([mine])
    views = svc.community_ideas()
    assert {v.idea.source for v in views} == {"Comunidad", "Otra"}
    other = next(v for v in views if v.idea.source == "Otra")
    assert other.idea.trader is None and other.fits                                           # también se compara con los perfiles de escaneo
    assert svc.feed_stamp().endswith("|v1") and "|" in svc.feed_stamp()                       # la marca junta las de todas las fuentes


# ---- los contratos de las fuentes en el Histórico ------------------------------------------------------------------------
def feed_rows(svc, table="scan_log"):
    return [dict(r) for r in svc.scan_log_repo.db.conn.execute(f"SELECT * FROM {table} WHERE origin = 'Comunidad' ORDER BY id")]


async def test_the_contracts_of_a_feed_are_logged_with_trader_origin_and_open_date(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    client.get("/scanner?reset=1")                                                       # registra los perfiles
    contract = svc.contracts.list("AAPL")[0]
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    summary = await svc.scan_log_now()
    rows = [r for r in feed_rows(svc) if r["ticker"] == "AAPL"]
    assert rows and {r["trader"] for r in rows} == {"Ana"} and {r["opened"] for r in rows} == {"2026-10-09"}      # su fecha, no la de first_seen
    assert all(r["active"] == 1 and r["profile"] for r in rows)
    names = set(svc.fit_names()) | {"Sin perfil"}
    assert {r["profile"] for r in rows} <= names and summary["appeared"] >= len(rows)
    assert all(r["trader"] == "Ana" for r in feed_rows(svc, "scan_events") if r["ticker"] == "AAPL" and r["event"] == "aparece")
    # el escaneo de perfiles no se mezcla: sus episodios siguen siendo `scan` y sin trader
    assert svc.scan_log_repo.active("Sin perfil", "put") == [] and all(r.trader is None for r in svc.scan_log_repo.all_active("scan"))
    before = len(feed_rows(svc, "scan_events"))
    await svc.scan_log_now()
    assert len(feed_rows(svc, "scan_events")) == before                                  # nada cambió: ni apariciones ni desapariciones
    # el contrato sale de la fuente: desaparece con su motivo y queda cerrado
    write(path, ideas_payload(contract).replace('"ticker": "AAPL"', '"ticker": "ZZZ"'), bump=60)
    await svc.scan_log_now()
    gone = [r for r in feed_rows(svc, "scan_events") if r["ticker"] == "AAPL" and r["event"] == "desaparece"]
    assert gone and {r["reason"] for r in gone} == {"ya no está en la fuente"}
    assert not [r for r in feed_rows(svc) if r["ticker"] == "AAPL" and r["active"]]


async def test_a_feed_without_data_does_not_make_its_contracts_vanish(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    refresh(client)
    client.get("/scanner?reset=1")
    contract = svc.contracts.list("AAPL")[0]
    path = tmp_path / "tortuga.json"
    write(path, ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(path)))
    await svc.scan_log_now()
    assert any(r["active"] for r in feed_rows(svc))
    path.unlink()                                                                        # el fichero desaparece: no se sabe nada de la fuente
    await svc.scan_log_now()
    assert any(r["active"] for r in feed_rows(svc)) and not [r for r in feed_rows(svc, "scan_events") if r["event"] == "desaparece"]


def test_the_historico_filters_by_origin_and_trader_and_the_sheet_gets_the_new_columns(client_and_service):
    from scanner_opciones.jobs.scan_log_sheets import ACTIVE_HEADER, EVENTS_HEADER, RUNS_HEADER, active_row, event_row, run_row
    from scanner_opciones.storage.scan_log import Hit
    client, svc, gw, _ = client_and_service
    repo = svc.scan_log_repo
    repo.apply("2026-10-30T10:00:00", "TPC", "put", [Hit("AAPL", "2026-12-18", 90.0, None, 1.2, 25.0, 35, 12.0)], [], [], 0)
    repo.apply("2026-10-30T11:00:00", "TPC", "put", [Hit("MSFT", "2026-12-18", 300.0, None, 2.0, 20.0, 35, 10.0, trader="Ana", opened="2026-10-29")], [], [], 0,
               origin="Comunidad")
    assert repo.origins() == ["Comunidad", "scan"] and repo.traders() == ["Ana"]
    assert [e["ticker"] for e in repo.recent_events(origin="Comunidad")] == ["MSFT"] and [e["ticker"] for e in repo.recent_events(trader="Ana")] == ["MSFT"]
    assert [e["ticker"] for e in repo.recent_events(origin="scan")] == ["AAPL"] and len(repo.recent_runs(origin="Comunidad")) == 1
    ev = event_row(repo.recent_events(origin="Comunidad")[0])
    assert len(ev) == len(EVENTS_HEADER) and ev[-3:] == ["Comunidad", "Ana", "2026-10-29"]
    act = active_row(repo.all_active(origin="Comunidad")[0])
    assert len(act) == len(ACTIVE_HEADER) and act[-3:] == ["Comunidad", "Ana", "2026-10-29"]
    assert len(run_row(repo.recent_runs()[0])) == len(RUNS_HEADER) and EVENTS_HEADER[-3:] == ["Origen", "Trader", "Abierto el"]
    page = client.get("/historico?origen=Comunidad&trader=Ana").text
    assert "MSFT" in page and "AAPL" not in page.split("<table")[1]
    assert "AAPL" in client.get("/historico?origen=scan").text.split("<table")[1]


# ---- las fuentes de contratos como fuente de tickers del Universo -----------------------------------------------------------
def ideas_json(generated, ideas):
    return json.dumps({"version": 1, "generated_at": generated, "tickers": [], "ideas": ideas})


def one_idea(ticker, trader="Ana", strike=150):
    return {"date": "2026-10-09", "trader": trader, "ticker": ticker, "strategy": "Short Put", "expiry": "2026-11-20",
            "short_strike": strike, "long_strike": None, "contracts": 1, "credit": 1.5}


async def test_a_contract_feed_publishes_its_tickers_as_a_universe_source_of_kind_contracts(tmp_path):
    svc = make(tmp_path)
    assert await svc.sync_feed_sources() == {}                                       # sin datos: nada
    write(tmp_path / "tortuga.json", ideas_json("2026-10-12T13:00:00", [one_idea("PEP"), one_idea("MCD", "Beto"), one_idea("PEP", "Beto", 140)]))
    res = await svc.sync_feed_sources()
    assert list(res) == ["Comunidad_Contratos"] and svc.manual_sources["Comunidad_Contratos"] == ["MCD", "PEP"]
    src = next(s for s in svc.universe_sources if s.name == "Comunidad_Contratos")
    assert src.kind == "contracts" and [c.name for c in src.table.columns][-2:] == ["Contratos", "Traders"]
    pep = next(r for r in src.table.rows if r.ticker == "PEP")
    assert [c.name for c in src.table.columns].index("Contratos") and "Ana, Beto" in str(pep.cells) and "2" in str(pep.cells)
    assert all(s.kind == "tickers" for s in svc.universe_sources if s.name != "Comunidad_Contratos")
    assert "Comunidad_Contratos" in svc.managed_sources() and not svc.is_renamable_source("Comunidad_Contratos")
    assert await svc.sync_feed_sources() == {}                                       # misma versión: no se repite
    write(tmp_path / "tortuga.json", ideas_json("2026-10-13T13:00:00", [one_idea("ASTS")]), bump=60)
    again = await svc.sync_feed_sources()
    assert svc.manual_sources["Comunidad_Contratos"] == ["ASTS"] and again["Comunidad_Contratos"]["removed"] == ["MCD", "PEP"]      # se sustituye entera
    assert "contratos vigentes" in svc.feed_source_note("Comunidad_Contratos") and svc.feed_source_note("TortugaTrades_Dia") is None


async def test_the_contracts_source_of_the_community_can_be_switched_off(tmp_path):
    svc = make(tmp_path, contracts_source=False)
    write(tmp_path / "tortuga.json", ideas_json("2026-10-12T13:00:00", [one_idea("PEP")]))
    assert await svc.sync_feed_sources() == {} and "Comunidad_Contratos" not in svc.manual_sources


async def test_another_feed_gets_its_own_universe_source_and_permanent_source(client_and_service, tmp_path):
    from datetime import date
    from scanner_opciones.community.ideas import idea_key
    from scanner_opciones.feeds.base import ContractIdea
    client, svc, gw, _ = client_and_service
    refresh(client)
    client.get("/scanner?reset=1")
    contract = svc.contracts.list("AAPL")[0]
    write(tmp_path / "tortuga.json", ideas_payload(contract))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(tmp_path / "tortuga.json")))
    mine = ContractIdea(date(2026, 10, 9), "Carla", "AAPL", "Short Put", contract.expiry, contract.strike, None, 1, 1.0, None, source="Otra")
    svc.feeds["Otra"] = StaticFeed([mine])
    res = await svc.sync_feed_sources()
    assert set(res) == {"Comunidad_Contratos", "Otra_Contratos"} and svc.manual_sources["Otra_Contratos"] == ["AAPL"]
    views = {v.idea.source: v for v in svc.community_ideas() if v.idea.ticker == "AAPL"}
    out = await svc.add_feed_perm([idea_key(views["Otra"].idea), idea_key(views["Comunidad"].idea)])
    assert svc.manual_sources["Otra_Perm"] == ["AAPL"] and svc.manual_sources["TortugaTrades_Perm"] == ["AAPL"]       # cada una, en la suya
    assert svc.feed_perm("Otra")["AAPL"]["traders"] == ["Carla"] and svc.community_perm()["AAPL"]["traders"] == ["Ana"]
    assert "Otra_Perm" in out["source"] and "TortugaTrades_Perm" in out["source"]
    page = client.get("/universe").text
    assert ">contratos<" in page
    assert "contratos vigentes" in client.get("/universe?src=Otra_Contratos").text


def test_the_config_page_separates_scan_profiles_from_contract_feeds_and_saves_whether_they_publish_tickers(client_and_service, tmp_path):
    client, svc, gw, _ = client_and_service
    write(tmp_path / "tortuga.json", ideas_json("2026-10-12T13:00:00", [one_idea("PEP"), one_idea("MCD", "Beto")]))
    object.__setattr__(svc.settings, "community", CommunitySettings(path=str(tmp_path / "tortuga.json")))
    svc.feeds["Otra"] = StaticFeed([])
    page = client.get("/scanner/config").text
    assert "Perfiles de escaneo" in page and "Fuentes de contratos" in page and 'name="feed" value="Comunidad"' in page and 'name="feed" value="Otra"' in page
    info = {f["name"]: f for f in svc.contract_feeds_info()}
    assert (info["Comunidad"]["contracts"], info["Comunidad"]["traders"], info["Comunidad"]["tickers"]) == (2, 2, 2) and info["Comunidad"]["publish"] is True
    assert info["Otra"]["contracts"] == 0 and info["Otra"]["stamp"] == "v1"
    r = client.post("/scanner/feeds", data={"feed": ["Comunidad", "Otra"], "publish_Otra": "on"}, follow_redirects=False)     # la comunidad deja de publicar
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert svc.feed_publishes("Comunidad") is False and svc.feed_publishes("Otra") is True and "Comunidad_Contratos" not in svc.manual_sources
    client.post("/scanner/feeds", data={"feed": ["Comunidad", "Otra"], "publish_Comunidad": "on"}, follow_redirects=False)
    assert svc.feed_publishes("Comunidad") is True and svc.manual_sources["Comunidad_Contratos"] == ["MCD", "PEP"]      # al activarla se publica al guardar
    assert "Comunidad_Contratos" in client.get("/scanner/config").text
