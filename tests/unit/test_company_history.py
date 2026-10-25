"""Historial de resultados (BPA real frente a estimación) y dividendos de la ficha de los gráficos."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.marketdata.history import (AnnualYear, Dividend, EarningsQuarter, FakeDividends, FakeEarningsHistory, beat_summary, last_quarters)
from scanner_opciones.marketdata.history_html import dividend_line, history_html
from scanner_opciones.marketdata.tastytrade import dividend_from_item
from scanner_opciones.marketdata.yahoo import earnings_from_frame
from scanner_opciones.storage.db import Database

TODAY = date(2026, 10, 24)


def q(month, actual, estimate):
    return EarningsQuarter(date(2026, month, 15), actual, estimate)


def test_beat_summary_counts_met_or_beaten_and_ignores_missing_estimates():
    rows = [q(1, 0.5, 0.4), q(2, 0.3, 0.3), q(3, 0.2, 0.25), q(4, 0.1, None)]
    assert beat_summary(rows) == (2, 3)             # igualar cuenta como cumplir; sin estimación no se compara
    assert beat_summary([q(4, 0.1, None)]) is None


def test_last_quarters_keeps_the_latest_with_actual():
    rows = [q(m, 0.1 * m, None) for m in range(1, 9)] + [EarningsQuarter(date(2026, 12, 1), None, 0.9)]
    assert [r.day.month for r in last_quarters(rows, 5)] == [4, 5, 6, 7, 8]


def test_yahoo_frame_skips_the_upcoming_report_and_nan_estimates():
    frame = pd.DataFrame({"EPS Estimate": [1.98, 1.89, float("nan")], "Reported EPS": [float("nan"), 2.02, 1.5]},
                         index=pd.to_datetime(["2026-11-02", "2026-07-30", "2026-04-30"]))
    rows = earnings_from_frame(frame, 5)
    assert [(r.day, r.actual, r.estimate) for r in rows] == [(date(2026, 4, 30), 1.5, None), (date(2026, 7, 30), 2.02, 1.89)]
    assert earnings_from_frame(None, 5) == []


def test_tastytrade_dividend_maps_fraction_and_ignores_sentinel_dates():
    ko = SimpleNamespace(dividend_yield=0.0222, dividend_rate_per_share=0.53, dividend_ex_date=date(2026, 12, 1), dividend_pay_date=date(2026, 12, 15))
    assert dividend_from_item(ko) == Dividend(2.22, 0.53, date(2026, 12, 1), date(2026, 12, 15))
    sofi = SimpleNamespace(dividend_yield=None, dividend_rate_per_share=None, dividend_ex_date=date(1970, 1, 1), dividend_pay_date=None)
    assert dividend_from_item(sofi).empty


def test_html_shows_ratio_dividend_and_escapes():
    html = history_html([q(1, 0.5, 0.4), q(2, 0.3, 0.4), q(3, 0.6, 0.5)], Dividend(2.2, 0.53, date(2026, 12, 1), date(2026, 12, 15)), TODAY)
    assert "2/3" in html and "<svg" in html and "2,20 %" in html and "próximo" in html and "pago 15/12/2026" in html
    assert history_html([], None, TODAY) == ""
    assert "último" in dividend_line(Dividend(1.0, None, date(2026, 8, 1), None), TODAY)


async def test_update_company_history_refreshes_only_what_is_due():
    now = [datetime(2026, 10, 22, 12, 0)]                    # jueves
    earnings = FakeEarningsHistory({"KO": [q(7, 0.97, 0.93)], "ETF": []})
    dividends = FakeDividends({"KO": Dividend(2.2, 0.53, date(2026, 12, 1), date(2026, 12, 15)), "ETF": Dividend()})
    svc = AppService(FakeGateway(), Database(), Settings(), lambda: now[0], earnings_history=earnings, dividends=dividends)
    assert await svc.daily.update_company_history(["KO", "ETF"]) == 4
    assert await svc.daily.update_company_history(["KO", "ETF"]) == 0                   # recién consultados
    now[0] += timedelta(hours=25)
    assert await svc.daily.update_company_history(["KO"]) == 2
    card = svc.history_card("KO")
    assert "1/1" in card and "15/12/2026" in card
    assert svc.history_card("ETF") == "" and svc.history_card("ZZZ") == ""


def test_annual_chart_has_margin_line_and_handles_losses_and_gaps():
    years = [AnnualYear(date(2022, 12, 31), 30e9, 9e9), AnnualYear(date(2023, 12, 31), 28e9, 5e9), AnnualYear(date(2024, 12, 31), 25e9, -2e9),
             AnnualYear(date(2025, 12, 31), 27e9, None)]
    assert round(years[0].net_margin_pct, 1) == 30.0 and years[3].net_margin_pct is None
    html = history_html([], None, TODAY, years)
    assert "<polyline" in html and "Resultados anuales" in html and "-8,00 %" in html and "SEC EDGAR" in html
    assert history_html([], None, TODAY, [AnnualYear(date(2025, 12, 31))]) == ""       # sin ningún importe no hay gráfico


def test_edgar_extracts_annual_results_from_company_facts():
    from scanner_opciones.marketdata.edgar import _annual_results

    def fact(end, val):
        return {"start": f"{end[:4]}-01-01", "end": end, "val": val, "form": "10-K", "filed": end}

    tree = {"Revenues": {"units": {"USD": [fact("2023-12-31", 10e9), fact("2024-12-31", 12e9)]}},
            "NetIncomeLoss": {"units": {"USD": [fact("2023-12-31", 1e9), fact("2024-12-31", -1e9)]}}}
    tags = {"revenue": ("Revenues",), "net_income": ("NetIncomeLoss",)}
    got = _annual_results(tree, tags, "2024-01-01")
    assert [(y.end.year, y.revenue, y.net_income) for y in got] == [(2023, 10e9, 1e9), (2024, 12e9, -1e9)]
    assert _annual_results(tree, tags, "2030-01-01") == ()                  # datos viejos: nada



async def test_empty_earnings_are_retried_after_hours_not_a_day():
    now = [datetime(2026, 10, 22, 12, 0)]                    # jueves
    earnings = FakeEarningsHistory({"KO": [q(7, 0.97, 0.93)], "XP": []})      # Yahoo limitó las peticiones y XP salió vacío
    svc = AppService(FakeGateway(), Database(), Settings(), lambda: now[0], earnings_history=earnings)
    await svc.daily.update_company_history(["KO", "XP"])
    first = len(earnings.calls)
    now[0] += timedelta(hours=7)                             # > earnings_empty_retry_hours (6) y < earnings_refresh_hours (24)
    earnings.data["XP"] = [q(7, 2.49, 2.53)]
    await svc.daily.update_company_history(["KO", "XP"])
    assert [c[1] for c in earnings.calls[first:]] == [("XP",)]            # KO (con datos) no se vuelve a pedir
    assert svc.company_history.earnings()["XP"][1] != []
