"""Filtro de crédito mínimo de la put desnuda ($ por contrato) y su carga en los perfiles TPC y TastyPut."""
from urllib.parse import parse_qsl

from scanner_opciones.config.settings import Settings
from scanner_opciones.scanner.filters import reject_reason
from scanner_opciones.ui.web import preset_link, seed_profiles, strategy_link
from tests.unit.test_scanner_filters import BASE, REGULAR, TODAY, snap


def test_credit_filter_uses_reference_price_times_multiplier():
    # strike 78 con yield 1,2 % = prima de 0,936 por acción = 93,6 $ por contrato
    crit = REGULAR.with_filters(min_put_credit=100)
    assert "crédito" in reject_reason(snap(), 100.0, TODAY, crit)
    assert reject_reason(snap(), 100.0, TODAY, REGULAR.with_filters(min_put_credit=90)) is None
    assert reject_reason(snap(), 100.0, TODAY, REGULAR) is None      # sin filtro no descarta


def test_credit_is_off_by_default():
    assert BASE.min_put_credit is None


def test_tpc_and_tastyput_profiles_load_the_credit_filter():
    settings = Settings()
    for preset in settings.scanner.presets:
        params = dict(parse_qsl(preset_link(settings, preset, {})[0]))
        assert params["use_credit"] == "on" and params["credit"] == "30"
    assert dict(parse_qsl(strategy_link(settings, {}, None)[0]))["credit"] == "30"
    profiles, _ = seed_profiles(settings)
    for p in profiles:
        if p.name in ("Mensual (TPC)", "Táctico (TPC)", "TastyPut"):
            assert p.set_params("put")["use_credit"] == "on"


def test_credit_can_be_disabled_per_profile():
    settings = Settings()
    preset = settings.scanner.presets[0].model_copy(update={"min_credit_usd": None})
    assert "use_credit" not in dict(parse_qsl(preset_link(settings, preset, {})[0]))
