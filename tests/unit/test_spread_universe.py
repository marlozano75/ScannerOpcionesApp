"""Universo para bull put spreads: ETF exentos de calidad, fuentes aptas para spreads (solo spread, nunca put desnuda) y composición diaria."""
import json
from datetime import date, datetime, timedelta

import pytest

from scanner_opciones.config.settings import Settings, SpreadUniverseSettings
from scanner_opciones.domain.enums import OptionRight, PriceReference
from scanner_opciones.domain.models import ContractSnapshot, OptionContract, TickerInfo
from scanner_opciones.scanner.criteria import criteria_from_settings
from scanner_opciones.scanner.engine import run_scan
from scanner_opciones.scanner.quality import is_exempt, spread_universe_reject, ticker_quality_reject
from scanner_opciones.universe_bps.builder import RadarRow, parse_radar, parse_seed, reject_reason, update, warnings
from scanner_opciones.universe_bps.data import Member, UniverseBpsError, parse_universe

CFG = SpreadUniverseSettings()
QCFG = Settings().scanner.quality
BASE = criteria_from_settings(Settings())


# ---- ETF exentos de la calidad de la empresa --------------------------------------------------
def test_etf_without_eps_passes_company_filters_but_a_stock_does_not():
    c = BASE.with_filters(require_profitable=True, min_positive_quarters=3, max_liabilities_to_equity=1.0, require_positive_fcf=True)
    assert ticker_quality_reject(TickerInfo("SPY"), c, QCFG.exempt_entries) is None
    assert ticker_quality_reject(TickerInfo("ACME"), c, QCFG.exempt_entries) is not None
    assert is_exempt(TickerInfo("QQQ"), QCFG.exempt_entries) and not is_exempt(TickerInfo("QQQQ"), QCFG.exempt_entries)


def test_etf_still_needs_option_liquidity():
    c = BASE.with_filters(min_option_liquidity=3)
    assert ticker_quality_reject(TickerInfo("SPY", option_liquidity=2), c, QCFG.exempt_entries) is not None
    assert ticker_quality_reject(TickerInfo("SPY", option_liquidity=4), c, QCFG.exempt_entries) is None


def test_sector_names_never_match_the_exact_ticker_entries():
    assert not is_exempt(TickerInfo("ACME", sector="=ACME"), ("=SPY",))


# ---- fuentes aptas para spreads: liquidez y precio -----------------------------------------------
def test_spread_universe_filter_is_liquidity_and_price_only():
    assert spread_universe_reject(TickerInfo("X", option_liquidity=3, underlying_price=50), 3, 30) is None
    assert spread_universe_reject(TickerInfo("X", option_liquidity=2, underlying_price=50), 3, 30) is not None
    assert spread_universe_reject(TickerInfo("X", underlying_price=50), 3, 30) is not None       # sin liquidez no cumple
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4, underlying_price=20), 3, 30) is not None
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4), 3, 30) is None            # sin precio no se descarta
    assert spread_universe_reject(TickerInfo("X", option_liquidity=4, underlying_price=600), 3, 30, 500) is not None


# ---- solo spread, nunca put desnuda ---------------------------------------------------------------
TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, 0)
SBASE = BASE.with_filters(strike_below_pct_min=0, dte_min=1, dte_max=60, min_yield_pct=0.0, price_reference=PriceReference.BID,
                          min_bid=None, spread_mode=True, spread_widths=(5.0,), best_width=False)
INFO = {"ZZZ": TickerInfo("ZZZ", underlying_price=100.0)}


def snap(strike, bid, ask, ticker="ZZZ"):
    c = OptionContract(ticker, TODAY + timedelta(days=30), strike, OptionRight.PUT)
    return ContractSnapshot(c, NOW, bid=bid, ask=ask, delta=-0.2, iv=0.3, open_interest=300, spread_pct=5.0)


LEGS = [snap(90, 2.0, 2.2), snap(85, 0.5, 0.6)]


def test_spread_only_ticker_is_excluded_from_put_mode_but_not_spread_mode():
    only = frozenset({"ZZZ"})
    put = run_scan(LEGS, INFO, [], SBASE.with_filters(spread_mode=False), TODAY, spread_only=only, include_rejections=True)
    assert put.results == [] and "solo spreads" in next(iter(put.rejections.values()))
    assert run_scan(LEGS, INFO, [], SBASE.with_filters(spread_mode=False), TODAY).results       # sin la marca sí sale
    assert run_scan(LEGS, INFO, [], SBASE, TODAY, spread_only=only).spreads


def test_both_mode_shows_only_the_spread_for_a_spread_only_ticker_even_under_the_naked_limit():
    c = SBASE.with_filters(mixed=True, max_naked_strike=100.0)       # 90 ≤ 100: normalmente sería put desnuda
    normal = run_scan(LEGS, INFO, [], c, TODAY, nlv_usd=100_000.0).mixed
    assert {r.kind for r in normal} == {"put"}
    only = run_scan(LEGS, INFO, [], c, TODAY, nlv_usd=100_000.0, spread_only=frozenset({"ZZZ"})).mixed
    assert only and {r.kind for r in only} == {"spread"} and "solo spread" in only[0].why_spread


# ---- composición diaria: candidatos del radar con VRP, rendimiento, tendencia y movimiento esperado ----------
CORE = [Member("EEM", "ETF", "Emergentes", "A", False), Member("HYG", "ETF", "Bonos high yield", "A", False)]


def row(t, kind="Acción", liq=4, price=100.0, vrp=8.0, y=30.0, trend="bullish", em=12.0, sector="Technology"):
    return RadarRow(t, sector, liq, price, kind, vrp, y, trend, em, 40.0)


def names(members):
    return sorted(m.ticker for m in members)


def test_a_candidate_must_meet_every_radar_condition():
    ok = row("OK")
    assert reject_reason(ok, CFG) is None
    assert "liquidez" in reject_reason(row("A", liq=2), CFG)
    assert "precio" in reject_reason(row("A", price=29.0), CFG)
    assert "VRP" in reject_reason(row("A", vrp=0.0), CFG) and "VRP" in reject_reason(row("A", vrp=-3.0), CFG)
    assert "bajista" in reject_reason(row("A", trend="bearish"), CFG)
    assert "movimiento" in reject_reason(row("A", em=26.0), CFG)
    assert "put de 30Δ" in reject_reason(row("A", y=19.0), CFG)


def test_etfs_do_not_need_the_put30_yield_and_missing_data_does_not_discard():
    assert reject_reason(row("SPY", kind="ETF", y=12.7), CFG) is None                      # IV baja por naturaleza
    bare = RadarRow("X", "Technology", 3, None)                                              # solo liquidez
    assert reject_reason(bare, CFG) is None
    assert reject_reason(row("A", y=19.0), SpreadUniverseSettings(min_put30_yield_pct=10)) is None
    assert reject_reason(row("A", trend="bearish"), SpreadUniverseSettings(exclude_bearish=False)) is None
    assert reject_reason(row("A", em=40.0), SpreadUniverseSettings(max_expected_move_pct=None)) is None


def test_candidates_enter_at_once_with_the_defaults_and_the_fixed_etf_core_is_always_in():
    hist, members = update({}, CORE, set(), [row("AAPL"), row("SPY", kind="ETF", y=12.0), row("BAD", vrp=-1.0)], day(0), CFG)
    assert names(members) == ["AAPL", "EEM", "HYG", "SPY"]
    aapl = next(m for m in members if m.ticker == "AAPL")
    assert (aapl.level, aapl.kind) == ("B", "Acción") and "VRP +8.0" in aapl.note and "alcista" in aapl.note
    assert next(m for m in members if m.ticker == "SPY").level == "A"


MON = date(2026, 10, 12)


def day(n):
    """n-ésimo día hábil desde el lunes 12/10 (0 = lunes)."""
    d = MON
    while n:
        d += timedelta(days=1)
        n -= d.weekday() < 5
    return d


def test_a_member_stays_during_the_grace_days_with_its_last_data_and_leaves_after_exit_days():
    hist, _ = update({}, CORE, set(), [row("AAPL"), row("MSFT")], day(0), CFG)
    for n in range(1, 5):                                                                    # 4 días fuera: sigue
        hist, members = update(hist, CORE, set(), [row("MSFT")], day(n), CFG)
        assert "AAPL" in names(members)
    hist, members = update(hist, CORE, set(), [row("AAPL"), row("MSFT")], day(5), CFG)      # vuelve: reinicia
    for n in range(6, 10):
        hist, members = update(hist, CORE, set(), [row("MSFT")], day(n), CFG)
        assert "AAPL" in names(members)
    hist, members = update(hist, CORE, set(), [row("MSFT")], day(10), CFG)                  # 5 seguidos
    assert "AAPL" not in names(members) and "MSFT" in names(members)


def test_weekends_do_not_count():
    hist, _ = update({}, CORE, set(), [row("AAPL")], day(0), CFG)
    for n in range(1, 5):
        hist, members = update(hist, CORE, set(), [], day(n), CFG)                           # lun-vie fuera: 4 días
    for d in (date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 17)):                   # sábado y domingo
        hist, members = update(hist, CORE, set(), [], d, CFG)
    assert "AAPL" in names(members)
    hist, members = update(hist, CORE, set(), [], date(2026, 10, 19), CFG)                   # lunes: el quinto
    assert "AAPL" not in names(members)


def test_a_candidate_that_stops_qualifying_counts_as_absent():
    cfg = SpreadUniverseSettings(exit_days=2)
    hist, _ = update({}, CORE, set(), [row("AAPL")], day(0), cfg)
    hist, members = update(hist, CORE, set(), [row("AAPL", trend="bearish")], day(1), cfg)
    assert "AAPL" in names(members)
    hist, members = update(hist, CORE, set(), [row("AAPL", vrp=-2.0)], day(2), cfg)
    assert "AAPL" not in names(members)


def test_rebuilding_on_the_same_day_does_not_count_twice_but_adds_new_candidates():
    cfg = SpreadUniverseSettings(exit_days=2)
    hist, _ = update({}, CORE, set(), [row("AAPL"), row("MSFT")], day(0), cfg)
    for _ in range(3):
        hist, members = update(hist, CORE, set(), [row("MSFT"), row("NEW")], day(1), cfg)
    assert names(members) == ["AAPL", "EEM", "HYG", "MSFT", "NEW"]                          # AAPL: un solo día fuera


def test_longer_entry_streak_when_configured():
    cfg = SpreadUniverseSettings(entry_days=2)
    hist, members = update({}, CORE, set(), [row("AAPL")], day(0), cfg)
    assert "AAPL" not in names(members)
    hist, members = update(hist, CORE, set(), [row("AAPL")], day(1), cfg)
    assert "AAPL" in names(members)


def test_excluded_never_enter_and_a_core_etf_cannot_be_dropped_by_the_radar():
    hist, members = update({}, CORE, {"TQQQ"}, [row("TQQQ", kind="ETF"), row("EEM", kind="ETF", vrp=-5.0)], day(0), CFG)
    assert "TQQQ" not in names(members) and "EEM" in names(members)


def test_sector_warning():
    many = [Member(f"T{i}", "Acción", "Tecnología", "B") for i in range(4)] + [Member("X", "Acción", "Salud", "B")]
    assert warnings(many, CFG) and not warnings(many[:1] + [Member("X", "Acción", "Salud", "B")], SpreadUniverseSettings(max_sector_pct=60))



# ---- formatos ---------------------------------------------------------------------------------------
def doc(**over):
    base = {"version": 1, "generated_at": "2026-10-17T10:00:00+02:00",
            "members": [{"ticker": "spy", "kind": "ETF", "sector": "Índice", "level": "A"}, {"ticker": "HOOD", "kind": "Acción", "sector": "Fin", "level": "D"}]}
    return {**base, **over}


def test_universe_json_roundtrip_and_active_levels():
    u = parse_universe(doc())
    assert [m.ticker for m in u.active()] == ["SPY"] and u.etfs() == {"SPY"}


@pytest.mark.parametrize("bad", [
    dict(version=2), dict(generated_at="ayer"), dict(members=[]),
    dict(members=[{"ticker": "A", "kind": "ETF", "level": "Z"}]), dict(members=[{"ticker": "A", "kind": "Bono", "level": "A"}]),
    dict(members=[{"ticker": "A", "kind": "ETF", "level": "A"}, {"ticker": "a", "kind": "ETF", "level": "A"}]),
])
def test_invalid_universe_is_rejected(bad):
    with pytest.raises(UniverseBpsError):
        parse_universe(doc(**bad))


def test_radar_and_seed_parsing():
    rows = parse_radar({"version": 1, "rows": [{"ticker": "aapl", "sector": "Technology", "liquidity": 4, "price": 345.6},
                                               {"ticker": "SPY", "sector": "ETF", "liquidity": 4, "price": 700}, {"nope": 1}]})
    assert [(r.ticker, r.kind) for r in rows] == [("AAPL", "Acción"), ("SPY", "ETF")]
    with pytest.raises(UniverseBpsError):
        parse_radar({"version": 1, "rows": []})
    members, excluded = parse_seed({"version": 1, "excluded": ["xop"], "members": [{"ticker": "SPY", "kind": "ETF", "level": "A"}]})
    assert excluded == {"XOP"} and members[0].ticker == "SPY"


def test_the_seed_file_in_the_repo_is_valid_and_its_etfs_are_exempt_by_default():
    from pathlib import Path
    seed = json.loads((Path(__file__).resolve().parents[2] / "universo" / "seed_bps.json").read_text(encoding="utf-8"))
    members, excluded = parse_seed(seed)
    assert len(members) >= 10 and not any(m.radar for m in members) and "XOP" in excluded and not {m.ticker for m in members} & excluded
    etfs = {m.ticker for m in members if m.kind == "ETF"}
    assert etfs <= {s.upper() for s in QCFG.etf_index_symbols}
