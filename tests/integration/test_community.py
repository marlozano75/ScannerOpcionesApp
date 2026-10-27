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
    assert '<a href="/ideas" class=active>Ideas</a>' in page.replace('class="active"', "class=active") and "Ideas de la comunidad de Tortuga" in page
    assert "Vigente" in page and "Sin valorar: sin cotización de esas patas (no está en tu watchlist)" in page and "2 de 2 ideas · 1 vigentes" in page
    assert 'class="chart-link" data-ticker="AAPL"' in page and 'id="strike-dialog"' in page   # el ticker abre el gráfico del strike
    only = client.get("/ideas?solo=1").text
    assert "Vigente" in only and "ZZZ" not in only and "1 de 2 ideas" in only
    ana = client.get("/ideas?trader=Ana").text
    assert '<td class="txt">Ana</td>' in ana and '<td class="txt">Beto</td>' not in ana
    assert "Ninguna idea cumple los filtros" in client.get("/ideas?strategy=Bull+Put+Spread").text


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
    assert "Añadidos a TortugaTrades_Perm" in page and ">TTP<" in page and ">W<" in page
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
