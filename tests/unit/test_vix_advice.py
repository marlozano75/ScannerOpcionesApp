"""El VIX no filtra el scanner: da un aviso en el Panel (primas pobres, mercado nervioso o condiciones ideales)."""
from datetime import date, datetime, timedelta

from scanner_opciones.config.settings import Settings, StrategySettings
from scanner_opciones.domain.models import VixData
from scanner_opciones.scanner.criteria import ScanCriteria
from scanner_opciones.scanner.market import vix_advice, vix_is_falling

TODAY = date(2026, 10, 6)
CFG = Settings().scanner.strategy


def vix(current, closes=(20.0, 22.0, 21.0)):
    return VixData(current, [(TODAY - timedelta(days=len(closes) - i), c) for i, c in enumerate(closes)], [], datetime(2026, 10, 6, 12))


def test_the_vix_is_not_a_scan_criterion_any_more():
    assert not any("vix" in f for f in ScanCriteria.__dataclass_fields__)


def test_falling_means_below_the_average_of_the_last_three_closes():
    assert vix_is_falling(vix(19.0)) is True            # 19 < media(20, 22, 21) = 21
    assert vix_is_falling(vix(23.0)) is False
    assert vix_is_falling(vix(19.0, closes=(20.0,))) is None
    assert vix_is_falling(VixData()) is None


def test_advice_levels():
    assert vix_advice(None, CFG) is None and vix_advice(VixData(), CFG) is None
    low = vix_advice(vix(12.5), CFG)
    assert low.level == "warn" and "primas pobres" in low.text
    high = vix_advice(vix(35.0), CFG)
    assert high.level == "warn" and "nervioso" in high.text
    ideal = vix_advice(vix(19.0), CFG)                                           # 18–30 y bajando
    assert ideal.level == "ok" and "ideales" in ideal.text and "bajando" not in ideal.text.split(":")[1]
    rising = vix_advice(vix(23.0), CFG)
    assert rising.level == "info" and "subiendo" in rising.text
    calm = vix_advice(vix(16.0, closes=(15.0, 16.0, 17.0)), CFG)                 # aceptable pero por debajo de lo ideal
    assert calm.level == "info" and "aceptables" in calm.text


def test_the_thresholds_come_from_the_strategy_settings():
    cfg = StrategySettings(min_vix=18, ideal_vix_min=20, max_vix=25)
    assert vix_advice(vix(17.0), cfg).level == "warn"
    assert vix_advice(vix(26.0), cfg).level == "warn"
    assert vix_advice(vix(19.0), cfg).level == "info"                           # bajo el mínimo ideal de 20
