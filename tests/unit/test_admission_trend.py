from scanner_opciones.config.settings import AdmissionTpc
from scanner_opciones.metrics.technical import long_term_uptrend
from scanner_opciones.scanner.admission import tpc_trend_reject

BELOW_RISING_MA = [100 + i * 0.1 for i in range(299)] + [90.0]      # la MA200 sube, pero el cierre está por debajo


def test_admission_accepts_a_close_below_a_rising_sma200():
    assert tpc_trend_reject(BELOW_RISING_MA, AdmissionTpc()) is None


def test_the_spread_universe_still_requires_the_close_above_the_sma():
    ok, _ = long_term_uptrend(BELOW_RISING_MA, 200, 20, 100)
    assert ok is False
