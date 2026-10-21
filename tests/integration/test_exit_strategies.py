"""Sección Salidas: estrategia de salida editable por perfil (portfolio/exits.py, /exits)."""
import pytest

from scanner_opciones.config.settings import ManagementSettings
from scanner_opciones.portfolio import exits
from scanner_opciones.scanner.profiles import Profile, dump, load
from tests.integration.test_web import client_and_service  # noqa: F401

CFG = ManagementSettings()


def test_styles_give_the_documented_defaults_and_overrides_win():
    tasty, tpc = exits.defaults("tastytrade", CFG), exits.defaults("tpc", CFG)
    assert (tasty.take_profit_pct, tasty.review_dte, tasty.itm_action) == (50, 21, "close") and not tasty.holds_to_expiry
    assert tpc.holds_to_expiry and tpc.close_capture_pct == 90 and tpc.stop_multiple is None
    custom = exits.resolve("tpc", {"take_profit_pct": 75.0, "review_dte": None}, CFG)
    assert custom.take_profit_pct == 75 and custom.review_dte is None and not custom.holds_to_expiry
    assert exits.overrides_of("tpc", custom, CFG) == {"take_profit_pct": 75.0}          # solo lo que cambia


def test_validation_rejects_nonsense():
    base = exits.defaults("tastytrade", CFG)
    for bad in ({"take_profit_pct": 120.0}, {"take_profit_max_pct": 40.0}, {"stop_multiple": 1.0}, {"warn_position_pct": 20.0},
                {"take_profit_pct": None, "take_profit_max_pct": 60.0}):
        with pytest.raises(ValueError):
            exits.validate(exits.resolve("tastytrade", bad, CFG))
    assert exits.validate(base) == base


def test_profile_exits_survive_dump_and_load_and_junk_is_dropped():
    p = Profile("X", ("put",), exits={"take_profit_pct": 70.0, "review_dte": None, "itm_action": "hold"})
    loaded, _ = load(dump([p], "X"))
    assert loaded[0].exits == p.exits
    assert exits.check_overrides({"take_profit_pct": "x", "bogus": 1, "itm_action": "nope", "pin_dte": 3}) == {"pin_dte": 3}


def test_exits_page_describes_every_profile_and_saves_changes(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.save_profiles([Profile("Uno", ("put",), management="tastytrade"), Profile("Dos", ("spread",), management="tpc")], "Uno")
    page = client.get("/exits").text
    assert "Uno" in page and "Dos" in page and "Recoger beneficios al ganar el 50 %" in page and "No se recoge antes del vencimiento" in page

    form = {"n_profiles": "3", "pf0_style": "tastytrade", "pf0_take_profit_pct": "70", "pf0_take_profit_max_pct": "80", "pf0_review_dte": "",
            "pf1_style": "tpc", "pf2_style": "tastytrade"}
    r = client.post("/exits/save", data=form, follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    saved = {p.name: p for p in svc.stored_profiles()[0]}
    assert saved["Uno"].exits == {"take_profit_pct": 70.0, "take_profit_max_pct": 80.0, "review_dte": None}
    assert saved["Dos"].exits == {} and saved["Uno"].exit_rules(svc.settings.management).review_dte is None

    # un valor incoherente no guarda nada; cambiar de estilo descarta los cambios
    bad = client.post("/exits/save", data={**form, "pf0_take_profit_pct": "90", "pf0_take_profit_max_pct": "80"}, follow_redirects=False)
    assert "error=" in bad.headers["location"] and svc.stored_profiles()[0][0].exits == saved["Uno"].exits
    client.post("/exits/save", data={**form, "pf0_style": "tpc"}, follow_redirects=False)
    assert {p.name: p for p in svc.stored_profiles()[0]}["Uno"].exits == {}


def test_put_status_uses_the_profile_rules():
    from datetime import date
    from tests.unit.test_atr_and_management import TODAY, alerts, short_put
    from scanner_opciones.portfolio.management import put_status
    half = short_put(market_value=-100.0)                                             # 55 % ganado
    rules = exits.resolve("tastytrade", {"take_profit_pct": 80.0}, CFG)
    assert "Recoger beneficios" not in alerts(put_status(half, TODAY, 100.0, CFG, rules=rules)).get("ok", "")
    assert "Recoger beneficios" in alerts(put_status(half, TODAY, 100.0, CFG))["ok"]
