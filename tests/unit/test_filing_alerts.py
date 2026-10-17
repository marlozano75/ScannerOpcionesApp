"""Alertas de la SEC (8-K y avisos de retraso): clasificación, descarga con EDGAR simulado, guardado y marca en la interfaz."""
import asyncio
from datetime import date, datetime, timedelta

from scanner_opciones.marketdata.edgar import EdgarFinancialsProvider
from scanner_opciones.marketdata.filings import FilingAlert, classify_filings
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import FilingAlertRepo
from scanner_opciones.ui.web import sec_flag

TODAY = date(2026, 10, 17)


def recent(*rows):
    return {"form": [r[0] for r in rows], "filingDate": [r[1] for r in rows], "items": [r[2] for r in rows]}


def test_only_serious_items_inside_the_window_are_alerts():
    alerts = classify_filings(recent(("8-K", "2026-10-10", "4.02,9.01"), ("8-K", "2026-10-12", "2.02,7.01"), ("8-K", "2026-05-01", "4.01"),
                                     ("NT 10-Q", "2026-09-01", ""), ("10-K", "2026-10-01", ""), ("8-K/A", "2026-10-15", "4.01")), TODAY, 90)
    assert [(a.form, a.item) for a in alerts] == [("8-K", "4.01"), ("8-K", "4.02"), ("NT 10-Q", "")]     # más recientes primero
    assert "no son fiables" in alerts[1].label and classify_filings({}, TODAY, 90) == []


def test_provider_reads_submissions_and_unknown_tickers_come_back_empty():
    urls = []

    async def fetch(url):
        urls.append(url)
        if url.endswith("company_tickers.json"):
            return {"0": {"ticker": "SMR", "cik_str": 1822966}}
        return {"filings": {"recent": recent(("8-K", "2026-10-14", "4.01"))}}

    provider = EdgarFinancialsProvider("test@example.com", fetch_json=fetch, today=lambda: TODAY)
    found = asyncio.run(provider.get_filing_alerts(["SMR", "ZZZZ"], 90))
    assert [a.item for a in found["SMR"]] == ["4.01"] and found["ZZZZ"] == []
    assert any("submissions/CIK0001822966" in u for u in urls)


def test_repo_roundtrip_and_purge():
    repo = FilingAlertRepo(Database(":memory:"))
    a = FilingAlert(date(2026, 10, 14), "8-K", "4.01", "Cambio de auditor")
    repo.save({"SMR": [a], "AAPL": []}, datetime(2026, 10, 17, 9))
    got = repo.all()
    assert got["SMR"][1] == [a] and got["AAPL"][1] == []
    assert repo.purge_except(["SMR"]) == 1 and set(repo.all()) == {"SMR"}


def test_flag_shows_the_text_and_normalises_the_ticker():
    a = FilingAlert(TODAY - timedelta(days=3), "8-K", "4.02", "Las cuentas ya publicadas no son fiables")
    html = sec_flag({"BRKB": [a]}, "BRK B")
    assert "⚠" in html and "4.02" in html and sec_flag({"BRKB": [a]}, "AAPL") == "" and sec_flag(None, "X") == ""
