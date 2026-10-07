from pathlib import Path

import pytest

from scanner_opciones.config.settings import Settings, load_settings
from scanner_opciones.domain.enums import AccountMode
from scanner_opciones.domain.errors import ConfigError

EXAMPLE = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"


def write(tmp_path, text):
    p = tmp_path / "c.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_example_config_loads_with_agreed_defaults():
    s = load_settings(EXAMPLE)
    assert s.refresh.interval_minutes == 5
    ini = s.scanner.initial
    assert (ini.strike_below_pct_min, ini.strike_below_pct_max, ini.dte_min, ini.dte_max) == (10, 30, 1, 35)
    c = s.scanner.candidates
    assert (c.strike_below_pct_min, c.strike_below_pct_max, c.dte_max) == (10, 30, 35)
    assert s.risk.cushion_thresholds.normal_above == 40
    assert s.risk.cushion_thresholds.concern_above == 30
    assert s.diversification.weeks_ahead == 5
    assert s.ibkr.mode is AccountMode.PAPER and s.ibkr.port == 7497


def test_defaults_without_file_content(tmp_path):
    assert load_settings(write(tmp_path, "")) == Settings()


def test_live_port(tmp_path):
    s = load_settings(write(tmp_path, "ibkr:\n  mode: live\n"))
    assert s.ibkr.port == 7496


def test_missing_file():
    with pytest.raises(ConfigError, match="No existe"):
        load_settings("no_existe.yaml")


def test_invalid_yaml(tmp_path):
    with pytest.raises(ConfigError, match="YAML inválido"):
        load_settings(write(tmp_path, "a: [1, 2"))


def test_root_not_mapping(tmp_path):
    with pytest.raises(ConfigError, match="mapa"):
        load_settings(write(tmp_path, "- 1\n- 2\n"))


def test_unknown_key_rejected(tmp_path):
    with pytest.raises(ConfigError, match="refresh"):
        load_settings(write(tmp_path, "refresh:\n  minutos: 3\n"))


@pytest.mark.parametrize(
    "text",
    [
        "refresh:\n  interval_minutes: 0\n",
        "scanner:\n  initial:\n    dte_min: 40\n    dte_max: 30\n",
        "scanner:\n  initial:\n    strike_below_pct_min: 30\n    strike_below_pct_max: 10\n",
        "scanner:\n  operation:\n    regular_dte_min: 25\n",   # clave eliminada: la columna «Operación» ya no existe
        "scanner:\n  regular:\n    dte_min: 25\n",   # clave antigua: ya no existe
        "scanner:\n  candidates:\n    strike_below_pct_min: 30\n    strike_below_pct_max: 20\n",
        "scanner:\n  candidates:\n    dte_min: 70\n    dte_max: 60\n",
        "scanner:\n  filters:\n    min_iv_rank: 150\n",
        "risk:\n  cushion_thresholds:\n    normal_above: 30\n    concern_above: 40\n",
        "ibkr:\n  mode: demo\n",
    ],
)
def test_invalid_values(tmp_path, text):
    with pytest.raises(ConfigError, match="Configuración inválida"):
        load_settings(write(tmp_path, text))


def test_settings_are_immutable():
    with pytest.raises(Exception):
        Settings().refresh.interval_minutes = 1


def test_market_and_logging_defaults_and_yaml_times(tmp_path):
    from datetime import date, time
    yaml_text = "\n".join([
        "market:",
        "  open: \"09:45\"",
        "  holidays: [2026-11-26]",
        "logging:",
        "  ib_async_level: ERROR",
        "",
    ])
    s = load_settings(write(tmp_path, yaml_text))
    assert s.market.open == time(9, 45) and s.market.close == time(16, 0)
    assert s.market.holidays == [date(2026, 11, 26)] and s.market.pause_when_closed is True
    assert s.logging.ib_async_level == "ERROR" and Settings().logging.ib_async_level == "WARNING"


def test_delayed_data_sets_delay_and_minimum_refresh_interval():
    assert Settings().ibkr.delay_minutes == 0
    assert Settings().refresh_interval_minutes == 5
    s = Settings.model_validate({"ibkr": {"market_data_type": 3}})
    assert s.ibkr.delay_minutes == 15 and s.refresh_interval_minutes == 15
    s = Settings.model_validate({"ibkr": {"market_data_type": 3}, "refresh": {"interval_minutes": 20}})
    assert s.refresh_interval_minutes == 20


def test_presets_por_defecto_y_validacion(tmp_path):
    s = load_settings(EXAMPLE)
    a, b = s.scanner.presets
    assert (a.strike_below_pct_min, a.dte_min, a.dte_max, a.min_annual_yield_pct) == (10, 1, 15, 20)
    assert (b.strike_below_pct_min, b.dte_min, b.dte_max, b.min_annual_yield_pct) == (20, 16, None, 13)
    bad = tmp_path / "c.yaml"
    bad.write_text(
        "scanner:" + chr(10) + "  presets:" + chr(10)
        + "    - {name: x, strike_below_pct_min: 10, dte_min: 20, dte_max: 5, min_annual_yield_pct: 1}" + chr(10),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_settings(bad)


def test_logging_file_defaults_and_can_be_disabled(tmp_path):
    assert Settings().logging.file == "logs/scanner.log"
    s = load_settings(write(tmp_path, "logging:\n  file: null\n"))
    assert s.logging.file is None
