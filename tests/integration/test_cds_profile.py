"""Perfil «MTO CDS» (instrumento `cds` de los perfiles del Scanner): formulario editable, guardado, siembra única y redirección de `/cds`."""
from dataclasses import replace

from fastapi.testclient import TestClient

from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import CdsSettings
from scanner_opciones.scanner import cds_profile
from scanner_opciones.scanner.profiles import Profile
from scanner_opciones.ui.cds_form import parse_cds
from scanner_opciones.ui.web import create_app
from tests.integration.test_service import make_service


def _svc(enabled=True):
    svc, _ = make_service()
    svc.settings = svc.settings.model_copy(update={"scanner": svc.settings.scanner.model_copy(update={"cds": CdsSettings(enabled=enabled)})})
    return svc


def test_defaults_come_from_the_logbook():
    cfg = CdsSettings()
    assert cfg.max_debit_usd == 2150 and cfg.sma200_below_pct_min is None
    assert {5.0, 10.0, 30.0, 60.0, 70.0, 80.0, 100.0} <= set(cfg.width_options)


def test_form_defaults_and_narrowing_inside_the_catalog():
    svc = _svc()
    p = parse_cds(svc, {"submitted": "1", "dte_min": "100", "dte_max": "300", "otm_min": "10", "widths": "50, 60, 7",
                        "dmin": "150", "dmax": "1000", "use_ret": "on", "ret": "100"})
    assert p["error"] is None and p["cds"].dte_min == 200 and p["cds"].dte_max == 300 and p["cds"].long_otm_pct_min == 25
    assert p["cds"].width_options == (50.0, 60.0) and p["cds"].max_debit_usd == 1000 and p["cds"].sma200_below_pct_min is None
    assert any("DTE recortado" in n for n in p["notices"]) and any("7" in n and "catálogo" in n for n in p["notices"])
    assert parse_cds(svc, {"submitted": "1", "widths": "7"})["error"]                 # ningún ancho del catálogo
    assert parse_cds(svc, {"submitted": "1", "dmin": "abc"})["error"] and parse_cds(svc, {"submitted": "1", "dmin": "900", "dmax": "100"})["error"]
    assert parse_cds(svc, {})["cds"].width_options == svc.settings.scanner.cds.width_options        # sin consulta: valores de config


def test_profile_roundtrip_and_old_json_without_cds_key_still_loads():
    from scanner_opciones.scanner import profiles as profiles_mod
    prof = Profile(cds_profile.MTO_CDS, ("cds",), management="tpc").with_filters("cds", cds_profile.profile_params(_svc().settings))
    assert prof.mode == "cds" and prof.set_params("cds")["dmax"] == "2150" and prof.put == {} and prof.spread == {}
    loaded = profiles_mod.load(profiles_mod.dump([prof], prof.name))
    assert loaded is not None and loaded[0][0].cds == prof.cds and loaded[0][0].instruments == ("cds",)
    legacy = '{"profiles": [{"name": "A", "instruments": ["put"], "put": {"discount": "5"}, "spread": {}}], "default": "A"}'
    assert profiles_mod.load(legacy)[0][0].cds == {}


def test_seed_runs_once_and_does_not_come_back_after_deleting_it():
    svc = _svc()
    svc.save_profiles([Profile("Solo put", ("put",)).with_filters("put", {"discount": "5"})], "Solo put")
    assert svc.seed_mto_cds_profile() is True
    assert [p.name for p in svc.stored_profiles()[0]] == ["Solo put", cds_profile.MTO_CDS]
    svc.save_profiles([p for p in svc.stored_profiles()[0] if p.mode != "cds"], "Solo put")
    assert svc.seed_mto_cds_profile() is False and [p.name for p in svc.stored_profiles()[0]] == ["Solo put"]


def test_page_shows_profile_form_sma_column_and_saves_a_profile():
    svc = _svc()
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop)
    with TestClient(app) as client:
        client.portal.call(svc.wait_idle)
        page = client.get("/cds")             # dirección antigua: redirige al Scanner con el perfil CDS
        assert page.status_code == 200 and "MTO CDS" in page.text and 'name="dmax" value="2150"' in page.text
        assert 'name="widths"' in page.text and "Apagado por defecto" in page.text
        scanner = client.get("/scanner?reset=1").text
        assert 'href="/scanner?' in scanner and "mode=cds" in scanner and "MTO CDS" in scanner and 'href="/cds' not in scanner
        bad = client.get("/scanner?submitted=1&mode=cds&dmin=x")
        assert bad.status_code == 200 and "no es un número" in bad.text
        saved = client.post("/scanner/profile/save", data={"name": "CDS barato", "query": "mode=cds&submitted=1&dmax=700&widths=50%2C+60&best=on&dmin=150&dte_min=200&dte_max=500"},
                            follow_redirects=False)
        assert saved.status_code == 303 and saved.headers["location"].startswith("/scanner?") and "mode=cds" in saved.headers["location"]
        prof = next(p for p in svc.stored_profiles()[0] if p.name == "CDS barato")
        assert prof.mode == "cds" and prof.cds["dmax"] == "700" and prof.cds["best"] == "on"
        page = client.get("/scanner?mode=cds&profile=CDS+barato")
        assert 'name="dmax" value="700"' in page.text and "checked" in page.text.split('name="best"')[1][:30]
        assert client.get("/scanner/todos").status_code == 200
        assert client.get("/exits").status_code == 200 and client.get("/scanner/config").status_code == 200