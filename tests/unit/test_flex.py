"""IBKR Flex Query: fecha de apertura de las puts vendidas (portfolio/flex.py)."""
from datetime import date, datetime

import httpx
import pytest

from scanner_opciones.portfolio import flex
from scanner_opciones.portfolio.openings import parse_openings

TODAY = date(2026, 11, 2)


def trade(day, side, qty, strike=90, expiry="20261218", ticker="AAPL", put="P", cat="OPT", level=None, oc="O"):
    extra = f' levelOfDetail="{level}"' if level else ""
    return (f'<Trade assetCategory="{cat}" underlyingSymbol="{ticker}" symbol="{ticker} 261218P00090000" expiry="{expiry}" strike="{strike}" '
            f'putCall="{put}" buySell="{side}" quantity="{qty}" openCloseIndicator="{oc}" tradeDate="{day}" dateTime="{day};093000"{extra}/>')


def statement(*trades):
    return f'<FlexQueryResponse><FlexStatements><FlexStatement><Trades>{"".join(trades)}</Trades></FlexStatement></FlexStatements></FlexQueryResponse>'


def convert(*trades):
    data, warnings = flex.statement_to_openings(statement(*trades), TODAY, datetime(2026, 11, 2, 12))
    parse_openings(data)                       # el JSON producido es válido para el lector de aperturas
    return data["puts"], warnings


def test_open_short_put_gets_its_opening_date_and_calls_stocks_and_long_puts_are_ignored():
    puts, _ = convert(trade("20261013", "SELL", -2), trade("20261013", "SELL", -1, put="C"), trade("20261013", "BUY", 100, cat="STK"),
                      trade("20261014", "BUY", 1, strike=85))                        # compra de put: net > 0, no es una vendida
    assert puts == [{"date": "2026-10-13", "ticker": "AAPL", "expiry": "2026-12-18", "strike": 90.0, "qty": 2.0}]


def test_the_date_is_the_start_of_the_current_run_not_the_first_ever_opening():
    puts, _ = convert(trade("20260901", "SELL", -1), trade("20260910", "BUY", 1, oc="C"),        # abierta y cerrada
                      trade("20261020", "SELL", -1), trade("20261022", "SELL", -1))              # reabierta y ampliada
    assert [(p["date"], p["qty"]) for p in puts] == [("2026-10-20", 2.0)]


def test_closed_and_expired_puts_do_not_appear_and_scaling_in_keeps_the_first_day():
    puts, _ = convert(trade("20261001", "SELL", -1, strike=80), trade("20261005", "BUY", 1, strike=80, oc="C"),       # cerrada
                      trade("20260101", "SELL", -1, strike=70, expiry="20260116"),                                   # vencida
                      trade("20261013", "SELL", -1), trade("20261015", "SELL", -1))
    assert [(p["strike"], p["date"]) for p in puts] == [(90.0, "2026-10-13")]


def test_order_and_summary_rows_do_not_double_count_when_executions_are_present():
    puts, _ = convert(trade("20261013", "SELL", -2, level="ORDER"), trade("20261013", "SELL", -1, level="EXECUTION"), trade("20261013", "SELL", -1, level="EXECUTION"))
    assert puts[0]["qty"] == 2.0


def test_bad_rows_and_empty_reports_are_reported_not_fatal():
    puts, warnings = convert(trade("20261013", "SELL", -1, strike=0))
    assert puts == [] and any("descartada" in w for w in warnings)
    assert any("Trades" in w for w in convert()[1])
    with pytest.raises(flex.FlexError):
        flex.statement_to_openings("no es xml", TODAY)


def handler(responses):
    calls = iter(responses)
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200, text=next(calls))))


SENT = "<FlexStatementResponse><Status>Success</Status><ReferenceCode>99</ReferenceCode><Url>https://x/GetStatement</Url></FlexStatementResponse>"
FAIL = "<FlexStatementResponse><Status>Fail</Status><ErrorCode>{c}</ErrorCode><ErrorMessage>msg {c}</ErrorMessage></FlexStatementResponse>"


async def test_fetch_retries_transient_errors_and_returns_the_statement():
    xml = statement(trade("20261013", "SELL", -1))
    async with handler([FAIL.format(c=1001), SENT, FAIL.format(c=1019), xml]) as http:
        assert await flex.fetch_statement("t", "q", client=http, wait=0) == xml


async def test_fetch_raises_with_the_ibkr_reason_for_permanent_errors():
    async with handler([FAIL.format(c=1012)]) as http:
        with pytest.raises(flex.FlexError, match="1012: msg 1012"):
            await flex.fetch_statement("t", "q", client=http, wait=0)
    async with handler([FAIL.format(c=1001)] * 3) as http:
        with pytest.raises(flex.FlexError, match="3 intentos"):
            await flex.fetch_statement("t", "q", client=http, attempts=3, wait=0)
