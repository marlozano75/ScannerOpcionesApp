"""Perfiles de gestión (`portfolio/mgmt_profiles.py`): siembra, validación, carga y reglas de salida."""
import pytest

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.portfolio import mgmt_profiles as mp
from scanner_opciones.portfolio.mgmt_profiles import MgmtProfile

CFG = ManagementSettings()


def test_seed_links_the_three_profiles_to_their_sources():
    profiles = mp.seed("Comunidad", "MarketRadar")
    assert [p.name for p in profiles] == ["Comunidad", "Radar", "MTO"]
    by_source = {p.source: p.name for p in profiles if p.source}
    assert by_source == {"Comunidad": "Comunidad", "MarketRadar": "Radar"}
    assert sum(1 for p in profiles if p.catchall) == 1 and profiles[-1].catchall and profiles[-1].name == mp.CATCHALL


def test_validate_requires_one_catchall_and_unique_names():
    with pytest.raises(ValueError):
        mp.validate_profiles([MgmtProfile("A"), MgmtProfile("B")])            # sin catch-all
    with pytest.raises(ValueError):
        mp.validate_profiles([MgmtProfile("MTO", catchall=True), MgmtProfile("X", catchall=True)])   # dos catch-all
    with pytest.raises(ValueError):
        mp.validate_profiles([MgmtProfile("A"), MgmtProfile("a"), MgmtProfile("MTO", catchall=True)])  # nombres repetidos
    with pytest.raises(ValueError):
        mp.validate_profiles([MgmtProfile("MTO", catchall=True)], frozenset({"MTO"}))   # choca con un perfil de escaneo


def test_dump_and_load_roundtrip():
    profiles = [MgmtProfile("Comunidad", "tpc", "#123456", {"take_profit_pct": 50.0}, "Comunidad", False),
                MgmtProfile("MTO", "tastytrade", "", {}, "", True)]
    loaded = mp.load(mp.dump(profiles))
    assert loaded is not None
    assert [(p.name, p.management, p.color, p.source, p.catchall) for p in loaded] == [
        ("Comunidad", "tpc", "#123456", "Comunidad", False), ("MTO", "tastytrade", "", "", True)]
    assert loaded[0].exits == {"take_profit_pct": 50.0}


def test_load_returns_none_on_garbage_or_invalid():
    assert mp.load("") is None
    assert mp.load("not json") is None
    assert mp.load(mp.dump([MgmtProfile("A")])) is None          # sin catch-all → inválido


def test_exit_rules_follow_the_base_style_with_overrides():
    tpc = MgmtProfile("MTO", "tpc", catchall=True).exit_rules(CFG)
    assert tpc.holds_to_expiry and tpc.take_profit_pct is None
    custom = MgmtProfile("Comunidad", "tastytrade", exits={"take_profit_pct": 75.0}).exit_rules(CFG)
    assert custom.take_profit_pct == 75.0 and custom.review_dte == CFG.review_dte
