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
    for bad in ({"take_profit_pct": 120.0}, {"take_profit_max_pct": 40.0}, {"stop_multiple": 1.0},
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

    form = {"n_profiles": "2", "n_mgmt": str(len(svc.management_profiles())), "pf0_style": "tastytrade", "pf0_take_profit_pct": "70",
            "pf0_take_profit_max_pct": "80", "pf0_review_dte": "", "pf1_style": "tpc"}
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


def test_management_profiles_crud_and_assignment_of_other_positions(client_and_service):
    from datetime import timedelta
    from scanner_opciones.domain.enums import OptionRight
    from scanner_opciones.domain.models import OptionContract, Position
    client, svc, gw, _ = client_and_service
    # una call comprada suelta: aparece en la tabla única de gestión y se clasifica en el catch-all MTO
    opt = OptionContract("AAPL", svc.now().date() + timedelta(days=30), 200.0, OptionRight.CALL)
    svc.state.positions = [Position("AAPL", 1, 500.0, "Technology", opt, avg_cost=400.0)]
    page = client.get("/").text
    assert "Gestión de posiciones" in page and '<option value="MTO" selected>' in page      # clasificada en la misma tabla
    key = f"AAPL|{opt.expiry.isoformat()}|200"
    assert client.post("/management/profile", data={"key": key, "profile": "Comunidad"}, follow_redirects=False).status_code == 303
    assert svc.put_profiles()[key] == "Comunidad"
    assert '<option value="Comunidad" selected>' in client.get("/").text

    # crear, renombrar y borrar perfiles de gestión
    n = len(svc.management_profiles())
    r = client.post("/scanner/profiles/mgmt", data={"n_mgmt": str(n), "new_name": "Swing", "new_style": "tastytrade",
                                                    **{f"mf{i}_name": p.name for i, p in enumerate(svc.management_profiles())}},
                    follow_redirects=False)
    assert r.status_code == 303 and "ok=" in r.headers["location"]
    assert "Swing" in [p.name for p in svc.management_profiles()]
    # intentar borrar el catch-all (aunque se fuerce la casilla): se ignora, MTO sigue
    profs = svc.management_profiles()
    data = {"n_mgmt": str(len(profs)), **{f"mf{i}_name": p.name for i, p in enumerate(profs)}}
    data.update({f"mf{i}_del": "on" for i, p in enumerate(profs) if p.catchall})
    client.post("/scanner/profiles/mgmt", data=data, follow_redirects=False)
    assert "MTO" in [p.name for p in svc.management_profiles()]


def test_put_status_uses_the_profile_rules():
    from datetime import date
    from tests.unit.test_atr_and_management import TODAY, alerts, short_put
    from scanner_opciones.portfolio.management import put_status
    half = short_put(market_value=-100.0)                                             # 55 % ganado
    rules = exits.resolve("tastytrade", {"take_profit_pct": 80.0}, CFG)
    assert "Recoger beneficios" not in alerts(put_status(half, TODAY, 100.0, CFG, rules=rules)).get("ok", "")
    assert "Recoger beneficios" in alerts(put_status(half, TODAY, 100.0, CFG))["ok"]


def test_the_style_script_is_in_the_page_body_not_in_the_title(client_and_service):
    page = client_and_service[0].get("/exits").text
    assert "<title>Estrategias de salida" in page and "<script id=\"ex-defaults\"" not in page.split("</title>")[0]
    assert page.index('id="ex-defaults"') > page.index("</title>")


def test_custom_base_style_save_use_and_delete(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.save_profiles([Profile("Uno", ("put",), management="tastytrade"), Profile("Dos", ("put",), management="tpc")], "Uno")
    form = {"n_profiles": "2", "n_mgmt": str(len(svc.management_profiles())), "pf0_style": "tastytrade", "pf0_take_profit_pct": "70",
            "pf0_take_profit_max_pct": "", "pf0_review_dte": "14", "pf0_newstyle": "Mi estilo", "pf1_style": "tpc"}
    r = client.post("/exits/save", data=form, follow_redirects=False)
    assert "ok=" in r.headers["location"]
    uno = {p.name: p for p in svc.stored_profiles()[0]}["Uno"]
    assert uno.management == "Mi estilo" and uno.exits == {}
    rules = uno.exit_rules(svc.settings.management)
    assert (rules.take_profit_pct, rules.review_dte, rules.defense) == (70, 14, True)      # hereda de Tastytrade lo que no cambió
    assert exits.custom_styles()["Mi estilo"][0] == "tastytrade"
    page = client.get("/exits").text
    assert "Mi estilo" in page and "Estilos base" in page
    # el estilo propio sobrevive a un reinicio y no se borra mientras un perfil lo usa
    assert exits.load_custom(exits.dump_custom(exits.custom_styles())) == exits.custom_styles()
    assert "error=" in client.post("/exits/styles/delete", data={"name": "Mi estilo"}, follow_redirects=False).headers["location"]
    client.post("/exits/save", data={**form, "pf0_style": "tpc", "pf0_newstyle": ""}, follow_redirects=False)
    assert "ok=" in client.post("/exits/styles/delete", data={"name": "Mi estilo"}, follow_redirects=False).headers["location"]
    assert "Mi estilo" not in exits.custom_styles()
    with pytest.raises(ValueError):
        exits.validate_style_name("TPC")


def test_mto_exits_measure_spreads_on_the_risk_and_debit_spreads_on_what_was_paid():
    from dataclasses import replace
    from tests.unit.test_atr_and_management import TODAY, alerts, short_put
    from scanner_opciones.portfolio import mgmt_profiles
    from scanner_opciones.portfolio.management import put_status
    rules = mgmt_profiles.seed("c", "r")[-1].exit_rules(CFG)
    assert (rules.risk_take_profit_pct, rules.risk_stop_pct, rules.review_dte, rules.debit_gain_pct) == (40, 50, 21, 20)
    assert (rules.long_stop_pct, rules.long_review_dte) == (70, 90) and exits.validate(rules) == rules
    spread = lambda mv: replace(short_put(strike=90.0, days=30, market_value=mv), hedge_strike=88.0)     # ancho 2, crédito 1,10 → riesgo 0,90
    win = alerts(put_status(spread(-140.0), TODAY, 100.0, CFG, rules=rules))                              # vale 0,70: +44 % del riesgo
    assert "44 % del riesgo" in win["ok"]
    assert "Recoger beneficios" not in alerts(put_status(spread(-170.0), TODAY, 100.0, CFG, rules=rules)).get("ok", "")      # +33 %
    assert "56 % del riesgo" in alerts(put_status(spread(-320.0), TODAY, 100.0, CFG, rules=rules))["warn"]                    # vale 1,60: −56 %
    assert "del riesgo" not in " ".join(alerts(put_status(spread(-320.0), TODAY, 100.0, CFG)).values())                        # sin MTO no aplica


def test_mto_risk_target_only_counts_in_the_first_15_days_since_opening():
    from dataclasses import replace
    from datetime import timedelta
    from tests.unit.test_atr_and_management import TODAY, alerts, short_put
    from scanner_opciones.portfolio import mgmt_profiles
    from scanner_opciones.portfolio.management import put_status
    rules = mgmt_profiles.seed("c", "r")[-1].exit_rules(CFG)
    sp = replace(short_put(strike=90.0, days=30, market_value=-140.0), hedge_strike=88.0)       # +44 % del riesgo
    def ok(opened):
        return alerts(put_status(sp, TODAY, 100.0, CFG, rules=rules, opened_on=opened)).get("ok", "")
    assert "del riesgo" in ok(TODAY - timedelta(days=15)) and "del riesgo" in ok(None)        # dentro del plazo o sin fecha: avisa
    assert "del riesgo" not in ok(TODAY - timedelta(days=16))                                  # fuera del plazo: ya no
