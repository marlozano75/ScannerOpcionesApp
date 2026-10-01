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
    assert (s.scanner.regular.dte_min, s.scanner.regular.dte_max) == (25, 35)
    assert s.scanner.regular.strike_below_pct == 20 and s.scanner.tactical.strike_below_pct == 10
    assert s.scanner.tactical.dte_max == 15
    c = s.scanner.candidates
    assert (c.strike_below_pct_min, c.strike_below_pct_max, c.dte_max) == (10, 40, 45)
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
        "scanner:\n  regular:\n    dte_min: 40\n    dte_max: 30\n",
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
