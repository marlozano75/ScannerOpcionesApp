"""Estrategia de venta de puts (fase 3): IV/HV30 y cambio de la IV a 5 días (el VIX es solo un aviso: ver test_vix_advice)."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.enums import OptionRight
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.marketdata.tastytrade import to_metrics
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import TickerInfoRepo

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
BASE = criteria_from_settings(Settings()).with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0)


def snap(strike=90.0):
    c = OptionContract("AAPL", TODAY + timedelta(days=30), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=1.0, ask=1.1, delta=-0.22, iv=0.3, open_interest=500, spread_pct=5.0)


def test_tastytrade_units_are_normalised_hv_and_iv_in_percent_and_the_iv_change_in_points():
    item = SimpleNamespace(implied_volatility_index_rank="0.521134916", implied_volatility_percentile="0.6",
                           implied_volatility_30_day=Decimal("27.61"), historical_volatility_30_day=Decimal("20.15"),
                           implied_volatility_index_5_day_change=Decimal("0.010408222"))     # valores reales de AAPL
    m = to_metrics(item)
    assert (m.iv_rank, m.hv30, m.iv30, m.iv_change_5d) == (52.11, 20.15, 27.61, 1.04)
    assert m.iv30 / m.hv30 > 1.3
    bare = SimpleNamespace(implied_volatility_index_rank="0.5", implied_volatility_percentile=None)
    assert to_metrics(bare).hv30 is None and to_metrics(bare).iv_change_5d is None


def test_iv_hv_ratio_filter():
    c = BASE.with_filters(min_iv_hv_ratio=1.2)
    ok = TickerInfo("AAPL", iv30=27.61, hv30=20.15)                                  # 1,37
    assert reject_reason(snap(), 100.0, TODAY, c, info=ok) is None
    assert "IV/HV" in reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL", iv30=22.0, hv30=20.0))
    assert reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL")) is None      # sin dato no se puede evaluar: no descarta
    assert reject_reason(snap(), 100.0, TODAY, c) is None
    assert reject_reason(snap(), 100.0, TODAY, BASE) is None


def test_iv_change_filter_accepts_zero_or_negative_and_rejects_a_rising_iv():
    c = BASE.with_filters(max_iv_change_5d=0.0)
    assert reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL", iv_change_5d=-0.8)) is None
    assert reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL", iv_change_5d=0.0)) is None
    assert "ha subido" in reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL", iv_change_5d=1.04))
    assert reject_reason(snap(), 100.0, TODAY, c, info=TickerInfo("AAPL")) is None     # sin dato no descarta


def test_the_engine_passes_the_ticker_info_to_the_iv_filters():
    infos = {"AAPL": TickerInfo("AAPL", underlying_price=100.0, iv30=30.0, hv30=20.0, iv_change_5d=-1.0)}
    c = BASE.with_filters(min_iv_hv_ratio=1.2, max_iv_change_5d=0.0)
    assert len(run_scan([snap()], infos, [], c, TODAY).results) == 1
    assert len(run_scan([snap()], {"AAPL": TickerInfo("AAPL", underlying_price=100.0)}, [], c, TODAY).results) == 1   # sin dato, pasa
    bad = {"AAPL": TickerInfo("AAPL", underlying_price=100.0, iv30=20.0, hv30=20.0, iv_change_5d=-1.0)}
    assert run_scan([snap()], bad, [], c, TODAY).results == []                                                      # con dato malo, no


def test_iv30_falls_back_to_the_iv_index_when_tastytrade_gives_no_30_day_value():
    item = SimpleNamespace(implied_volatility_index_rank="0.5", implied_volatility_percentile="0.6", implied_volatility_30_day=None,
                           historical_volatility_30_day=None, implied_volatility_index="0.4063")
    assert to_metrics(item).iv30 == 40.63 and to_metrics(item).hv30 is None


def test_hv30_iv30_and_the_iv_change_are_stored_and_a_missing_value_does_not_erase_the_last_one():
    repo = TickerInfoRepo(Database(":memory:"))
    repo.upsert(TickerInfo("AAPL", underlying_price=100.0, hv30=20.15, iv30=27.61, iv_change_5d=1.04))
    got = repo.get("AAPL")
    assert (got.hv30, got.iv30, got.iv_change_5d) == (20.15, 27.61, 1.04)
    repo.update_iv_stats("AAPL", 50.0, 60.0, 21.0, None, None)                         # llegan solo el rank y la HV30
    got = repo.get("AAPL")
    assert (got.iv_rank, got.hv30, got.iv30, got.iv_change_5d) == (50.0, 21.0, 27.61, 1.04)


def test_migration_v19_adds_the_columns_without_losing_data(tmp_path):
    import sqlite3
    from scanner_opciones.storage.db import MIGRATIONS
    path = tmp_path / "v18.db"
    raw = sqlite3.connect(path)
    for version, script in enumerate(MIGRATIONS[:18], start=1):
        raw.executescript(script)
        raw.execute(f"PRAGMA user_version = {version}")
    raw.execute("INSERT INTO ticker_info (ticker, iv_rank) VALUES ('AAPL', 40)")
    raw.commit()
    raw.close()
    db = Database(path)
    row = db.conn.execute("SELECT * FROM ticker_info WHERE ticker = 'AAPL'").fetchone()
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 28
    assert row["iv_rank"] == 40 and row["hv30"] is None and {"hv30", "iv30", "iv_change_5d"} <= set(row.keys())


def test_expected_move_filter_requires_the_strike_below_price_minus_n_moves():
    c = BASE.with_filters(expected_move_mult=1.0)          # movimiento = 100 × 0,3 × √(30/365) ≈ 8,6 -> límite ≈ 91,4
    assert reject_reason(snap(90.0), 100.0, TODAY, c) is None
    assert "movimiento esperado" in reject_reason(snap(95.0), 100.0, TODAY, c)
    nov = ContractSnapshot(snap().contract, NOW, bid=1.0, ask=1.1, delta=-0.22, iv=None, open_interest=500, spread_pct=5.0)
    assert "sin IV" in reject_reason(nov, 100.0, TODAY, c)
    assert reject_reason(snap(95.0), 100.0, TODAY, BASE) is None


def test_expected_move_uses_the_iv_of_the_expiry_and_falls_back_to_the_contract_iv():
    c = BASE.with_filters(expected_move_mult=1.0)
    exp = snap().contract.expiry.isoformat()
    calm = TickerInfo("AAPL", expiry_ivs={exp: 0.1})          # movimiento 100 × 0,1 × √(30/365) ≈ 2,9 -> límite ≈ 97,1
    wide = TickerInfo("AAPL", expiry_ivs={exp: 0.6})          # movimiento ≈ 17,2 -> límite ≈ 82,8
    assert reject_reason(snap(95.0), 100.0, TODAY, c, info=calm) is None
    assert "movimiento esperado" in reject_reason(snap(90.0), 100.0, TODAY, c, info=wide)
    other = TickerInfo("AAPL", expiry_ivs={"2030-01-01": 0.6})      # sin IV para ese vencimiento: la del contrato (0,3)
    assert reject_reason(snap(90.0), 100.0, TODAY, c, info=other) is None


def test_expiry_ivs_come_from_the_market_metrics_and_are_stored_without_being_erased():
    item = SimpleNamespace(implied_volatility_index_rank="0.5", implied_volatility_percentile="0.6",
                           option_expiration_implied_volatilities=[
                               SimpleNamespace(expiration_date=date(2026, 10, 16), implied_volatility=Decimal("0.3074")),
                               SimpleNamespace(expiration_date=date(2026, 10, 19), implied_volatility=None)])
    m = to_metrics(item)
    assert m.expiry_ivs == {"2026-10-16": 0.3074}
    repo = TickerInfoRepo(Database(":memory:"))
    repo.upsert(TickerInfo("AAPL", underlying_price=100.0, expiry_ivs=m.expiry_ivs))
    repo.update_iv_stats("AAPL", 50.0, 60.0)                       # sin IV por vencimiento nuevas: se conservan las guardadas
    assert repo.get("AAPL").expiry_ivs == {"2026-10-16": 0.3074}
