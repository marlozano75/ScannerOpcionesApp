"""Fase 4: perfiles TastyPut / TastySpread separados y un solo escaneo con todos los perfiles y la columna de estrategias."""
import json
import re
from dataclasses import replace

from scanner_opciones.scanner.profiles import Profile
from tests.integration.test_web import client_and_service, refresh  # noqa: F401

LOOSE = {"discount": "10", "dte_min": "25", "dte_max": "35", "min_yield": "1"}
STRICT = {**LOOSE, "discount": "22"}


def test_seed_profiles_split_the_tastytrade_strategy_into_put_and_spread(client_and_service):
    client, svc, gw, _ = client_and_service
    page = client.get("/scanner?reset=1").text
    assert ">TastyPut</a>" in page and ">TastySpread</a>" in page and ">Tastytrade</a>" not in page
    names = re.findall(r'<a class="preset[^"]*" href="/scanner\?[^"]*profile=([^&"]+)', page)
    assert "TastyPut" in names and "TastySpread" in names
    assert 'href="/scanner/todos"' in page


def test_the_saved_tastytrade_profile_is_split_once_and_its_puts_follow(client_and_service):
    client, svc, gw, _ = client_and_service
    old = Profile("Tastytrade", ("put", "spread"), {"dte_min": "30"}, {"dte_min": "30", "rr": "25"}, color="#1f6feb")
    svc.save_profiles([Profile("Mensual (TPC)", ("put",)), old, Profile("Tortuga", ("put",))], "Tastytrade")
    svc.meta.set("put_profiles", json.dumps({"AAPL|2099-01-01|90": "Tastytrade", "MU|2099-01-01|50": "Mensual (TPC)"}))
    assert svc.split_tastytrade_profile() is True
    profiles, default = svc.stored_profiles()
    assert [(p.name, p.instruments) for p in profiles] == [("Mensual (TPC)", ("put",)), ("TastyPut", ("put",)), ("TastySpread", ("spread",)), ("Tortuga", ("put",))]
    assert default == "TastyPut" and all(p.color == "#1f6feb" for p in profiles if p.name.startswith("Tasty")) and profiles[2].spread == old.spread
    assert json.loads(svc.meta.get("put_profiles")) == {"AAPL|2099-01-01|90": "TastyPut", "MU|2099-01-01|50": "Mensual (TPC)"}
    assert svc.split_tastytrade_profile() is False                                      # ya dividido: no repite
    assert {p.name: p.management for p in profiles if p.name.startswith("Tasty")} == {"TastyPut": "tpc", "TastySpread": "tastytrade"}


def test_tastyput_moves_to_tpc_once_and_a_later_manual_change_is_respected(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.meta.set("mig_tastyput_tpc", "")
    svc.save_profiles([Profile("TastyPut", ("put",), {"dte_min": "30"}, {}), Profile("TastySpread", ("spread",), {}, {"dte_min": "30"})], "TastyPut")
    assert svc.tastyput_to_tpc() is True
    assert {p.name: p.management for p in svc.stored_profiles()[0]} == {"TastyPut": "tpc", "TastySpread": "tastytrade"}
    svc.save_profiles([replace(p, management="tastytrade") if p.name == "TastyPut" else p for p in svc.stored_profiles()[0]], "TastyPut")
    assert svc.tastyput_to_tpc() is False and svc.stored_profiles()[0][0].management == "tastytrade"       # ya migrado: no pisa lo elegido


def test_seed_profiles_manage_puts_with_tpc_and_spreads_with_tastytrade(client_and_service):
    from scanner_opciones.ui.web import seed_profiles
    profiles, _ = seed_profiles(client_and_service[1].settings)
    assert {p.name: p.management for p in profiles if p.name.startswith("Tasty")} == {"TastyPut": "tpc", "TastySpread": "tastytrade"}


def test_one_scan_lists_each_contract_once_with_every_profile_that_passes(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.save_profiles([Profile("Laxo", ("put",), LOOSE, {}, color="#112233"), Profile("Estricto", ("put",), STRICT, {}),
                       Profile("Tortuga", ("put",), LOOSE, {})], "Laxo")
    refresh(client)
    page = client.get("/scanner/todos").text
    assert "Todos los perfiles en un solo escaneo" in page and "<th" in page and ">Estrategias</th>" in page
    rows = re.findall(r'<tr><td class="sel">.*?</tr>', page, flags=re.S)
    assert rows, "debería haber contratos que cumplen los perfiles"
    keys = [re.search(r'name="sel" value="([^"]+)"', r).group(1) for r in rows]
    assert len(keys) == len(set(keys))                                                  # cada contrato, una sola vez
    both = [r for r in rows if "Laxo</span>" in r and "Estricto</span>" in r]
    only_loose = [r for r in rows if "Laxo</span>" in r and "Estricto</span>" not in r]
    assert both and only_loose and "Tortuga</span>" not in page.split("<table")[1]      # el perfil de las puts que no encajan no entra
    assert "background:#112233" in page                                                 # color del perfil en su etiqueta
    only = client.get("/scanner/todos?pf=Estricto").text
    assert "Laxo</span>" not in only.split("<table")[1] and "Estricto</span>" in only


def test_service_counts_rows_per_profile_and_merges_the_same_contract(client_and_service):
    client, svc, gw, _ = client_and_service
    refresh(client)
    a = svc.criteria().with_filters(strike_below_pct_min=10, dte_min=25, dte_max=35)
    b = svc.criteria().with_filters(strike_below_pct_min=20, dte_min=25, dte_max=35)
    rows, counts = svc.scan_profiles([("A", "put", a), ("B", "put", b)])
    assert counts["A"]["put"] >= counts["B"]["put"] > 0 and len(rows) == counts["A"]["put"]
    assert sum(1 for r in rows if r.profiles == ("A", "B")) == counts["B"]["put"]
    assert all(r.kind == "put" for r in rows)


def test_mixed_tables_show_credit_over_width_and_a_dash_for_naked_puts(client_and_service):
    client, svc, gw, _ = client_and_service
    svc.save_profiles([Profile("Laxo", ("put",), LOOSE, {})], "Laxo")
    refresh(client)
    page = client.get("/scanner/todos").text
    assert ">Créd/anch</th>" in page
    row = re.search(r'<tr><td class="sel">.*?</tr>', page, flags=re.S).group(0)
    assert "<td>Put</td>" in row and "<td>—</td><td>" in row                     # put desnuda: sin ancho ni crédito/ancho
    both = client.get("/scanner?submitted=1&mode=both&set=spread&discount=10&dte_min=25&dte_max=35&min_yield=1").text
    assert ">Créd/anch</th>" in both
