"""Ficha de empresa del tooltip: descripción del negocio y noticias con fecha (Yahoo + Finnhub), filtradas y guardadas."""
from datetime import date, datetime, timedelta

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.marketdata.company import (CompanyNotes, CompanyNotesError, FakeCompanyData, NewsItem, select_news,
                                                 short_description, tooltip)
from scanner_opciones.marketdata.company_cli import apply
from scanner_opciones.marketdata.finnhub import finnhub_symbol, parse_news
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import CompanyNotesRepo

TODAY = date(2026, 10, 24)
CFG = Settings().company


def news(*headlines, day=TODAY, related=1):
    return [NewsItem(day - timedelta(days=i), h, "Reuters", related=related) for i, h in enumerate(headlines)]


def pick(items, **kw):
    return select_news(items, TODAY, kw.get("window", 90), kw.get("max_items", 4), CFG.news_keywords, CFG.news_exclude)


def test_only_relevant_recent_unique_headlines_survive():
    items = news("Robinhood misses earnings estimates", "Here's why Robinhood stock fell today", "Robinhood sued by regulators",
                 "Bank of America opens new branch", "Robinhood misses earnings estimates") + news("Old probe", day=TODAY - timedelta(days=200))
    got = [n.headline for n in pick(items)]
    assert "Robinhood misses earnings estimates" in got and got.count("Robinhood misses earnings estimates") == 1
    assert not any("Here's why" in h or "Bank of America" in h or "Old probe" in h for h in got)


def test_keywords_match_whole_word_starts_and_market_roundups_are_dropped():
    assert pick(news("Urban Outfitters opens stores")) == []                        # «ban» no es «urban»
    assert pick(news("Company acquires rival")) != []                               # «acquir» vale para «acquires»
    assert pick(news("Fed holds rates; dividend stocks rally", related=9)) == []    # titular asociado a muchos tickers
    assert len(pick(news(*[f"Earnings beat number {i}" for i in range(9)]), max_items=3)) == 3


def test_description_is_cut_at_a_sentence():
    text = "Robinhood operates a platform. It offers fractional trading and margin. " + "Long tail. " * 40
    assert short_description(text, 50) == "Robinhood operates a platform."
    assert short_description("Corto.", 80) == "Corto." and short_description("x" * 500, 50).endswith("…")


def test_tooltip_prefers_what_was_written_by_hand_and_dates_the_news():
    auto = CompanyNotes("English text.", TODAY, tuple(news("Lawsuit filed")), TODAY)
    assert tooltip(auto, 300) == "English text.\n\n**Noticias** (actualizadas el 24/10/2026):\n· **24/10** Lawsuit filed (Reuters)"
    manual = CompanyNotes("English text.", TODAY, auto.news, TODAY, "Plataforma de bolsa.", (NewsItem(date(2026, 10, 20), "Lanza futuros"),), TODAY)
    assert tooltip(manual, 300) == "Plataforma de bolsa.\n\n**Noticias revisadas** (actualizadas el 24/10/2026):\n· **20/10** Lanza futuros"
    assert tooltip(CompanyNotes(), 300) == ""


def test_finnhub_rows_are_parsed():
    rows = [{"datetime": int(datetime(2026, 10, 23, 12).timestamp()), "headline": "Foo", "source": "Yahoo", "url": "u", "related": "HOOD,SOFI"},
            {"datetime": "mal", "headline": "x"}, {"datetime": 1, "headline": ""}]
    assert [(n.day, n.headline, n.related) for n in parse_news(rows)] == [(date(2026, 10, 23), "Foo", 2)]
    assert finnhub_symbol("BRK-B") == "BRK.B"


def test_repo_keeps_what_was_written_by_hand_on_automatic_refresh_and_purge():
    repo = CompanyNotesRepo(Database())
    repo.save_curated("HOOD", "Resumen", [NewsItem(date(2026, 10, 20), "Algo")], TODAY)
    repo.save_descriptions({"HOOD": "desc", "KO": "bebidas"}, TODAY)
    repo.save_news({"HOOD": news("Probe opened")}, TODAY)
    got = repo.all()
    assert got["HOOD"].summary == "Resumen" and got["HOOD"].description == "desc" and got["HOOD"].news[0].headline == "Probe opened"
    assert repo.purge_except([]) == 1 and set(repo.all()) == {"HOOD"}               # KO se borra; HOOD tiene resumen a mano


async def test_updater_requests_only_stale_tickers_and_keeps_what_it_has_when_finnhub_fails():
    now = [datetime(2026, 10, 21, 9, 0)]
    fake = FakeCompanyData({"HOOD": "Robinhood runs a trading platform."}, {"HOOD": news("HOOD sued by regulators", day=date(2026, 10, 20))})
    svc = AppService(FakeGateway(), Database(), Settings(), lambda: now[0], descriptions=fake, news=fake)
    assert await svc.daily.update_company_notes(["HOOD", "KO"]) == 3              # 1 descripción (KO no tiene) + 2 noticias
    assert "Robinhood runs a trading platform." in svc.company_notes_map()["HOOD"] and "sued" in svc.company_notes_map()["HOOD"]
    assert "KO" not in svc.company_notes_map()                                      # sin descripción ni noticias: no hay tooltip
    assert await svc.daily.update_company_notes(["HOOD", "KO"]) == 0                # recién consultados
    now[0] += timedelta(hours=25)
    fake.error = CompanyNotesError("clave mala")
    assert await svc.daily.update_company_notes(["HOOD"]) == 0
    assert "HOOD" in svc.company_notes_map()                                        # se conserva lo guardado


def test_cli_document_is_applied():
    repo = CompanyNotesRepo(Database())
    assert apply(repo, {"hood": {"summary": "Broker online", "news": [{"day": "2026-10-20", "text": "Futuros perpetuos", "source": "X"}]}}, TODAY) == 1
    c = repo.all()["HOOD"]
    assert c.summary == "Broker online" and c.curated[0].headline == "Futuros perpetuos"
    apply(repo, {"HOOD": {"news": []}}, TODAY)                                      # news vacío borra las revisadas, el resumen se queda
    c = repo.all()["HOOD"]
    assert c.summary == "Broker online" and c.curated == ()


def test_headlines_must_name_the_company_because_finnhub_mixes_in_other_companies():
    from scanner_opciones.marketdata.company import mention_pattern
    items = news("American Express Q3 earnings beat estimates", "Robinhood wins Dubai approval", "HOOD earnings jump")
    got = [n.headline for n in select_news(items, TODAY, 90, 4, CFG.news_keywords, CFG.news_exclude,
                                           mention=mention_pattern("HOOD", "Robinhood Markets, Inc. Class A Common Stock"))]
    assert got == ["Robinhood wins Dubai approval", "HOOD earnings jump"]
    assert mention_pattern("AXP", "American Express Company").search("American Express beats") and \
        not mention_pattern("AXP", "American Express Company").search("American Airlines sued")      # «American» solo no vale
    assert mention_pattern("KO", "Coca-Cola Co").search("Coca-Cola raises guidance") and not mention_pattern("KO").search("ko")


def test_card_has_full_name_description_and_dated_news_escaped():
    from scanner_opciones.marketdata.company import card_html
    notes = CompanyNotes("Plataforma <b>x</b>.", TODAY, tuple(news("Probe & lawsuit")), TODAY)
    html = card_html("Robinhood Markets, Inc.", notes, 300)
    assert "<b>Robinhood Markets, Inc.</b>" in html and "&lt;b&gt;x&lt;/b&gt;" in html and "Probe &amp; lawsuit" in html and "24/10/2026" in html
    assert card_html("", None, 300) == ""


def test_strike_chart_extends_to_upcoming_earnings_only_when_close():
    from scanner_opciones.metrics.technical import strike_history
    from scanner_opciones.ui.charts import strike_chart_html
    bars = [(TODAY - timedelta(days=400 - i), 100.0 + i * 0.1) for i in range(400)]
    h = strike_history(bars, 90.0, TODAY, 12)
    assert "resultados 3 nov · en 10 d" not in strike_chart_html(h, "ABC", 90.0, 5)           # sin fecha: sin raya
    html = strike_chart_html(h, "ABC", 90.0, 5, TODAY + timedelta(days=10))
    assert "Resultados el 3 nov 2026" in html and "Resultados 3 nov" in html
    assert "Resultados el" not in strike_chart_html(h, "ABC", 90.0, 5, TODAY + timedelta(days=300))
