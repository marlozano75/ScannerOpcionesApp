from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from scanner_opciones.config.settings import MarketSettings
from scanner_opciones.market.hours import MarketCalendar

NY = ZoneInfo("America/New_York")
MADRID = ZoneInfo("Europe/Madrid")
CAL = MarketCalendar(NY, holidays=frozenset({date(2026, 11, 26)}))


def ny(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def test_open_during_regular_session():
    assert CAL.is_open(ny(2026, 10, 1, 9, 30)) and CAL.is_open(ny(2026, 10, 1, 15, 59))


def test_closed_before_open_after_close_and_at_close_time():
    assert not CAL.is_open(ny(2026, 10, 1, 9, 29))
    assert not CAL.is_open(ny(2026, 10, 1, 16, 0))
    assert not CAL.is_open(ny(2026, 10, 1, 4, 56))        # el log del usuario: 10:56 en Madrid


def test_closed_on_weekends_and_holidays():
    assert not CAL.is_open(ny(2026, 10, 3, 12))           # sábado
    assert not CAL.is_open(ny(2026, 10, 4, 12))           # domingo
    assert not CAL.is_open(ny(2026, 11, 26, 12))          # Acción de Gracias


def test_timezone_conversion_from_aware_datetime():
    assert not CAL.is_open(datetime(2026, 10, 1, 10, 56, tzinfo=MADRID))   # 04:56 NY
    assert CAL.is_open(datetime(2026, 10, 1, 15, 30, tzinfo=MADRID))       # 09:30 NY
    assert CAL.is_open(datetime(2026, 10, 1, 21, 59, tzinfo=MADRID))
    assert not CAL.is_open(datetime(2026, 10, 1, 22, 0, tzinfo=MADRID))


def test_us_and_spanish_daylight_saving_differ_for_a_few_weeks():
    # EE. UU. cambia el 8-mar-2026 y España el 29-mar: entre medias la diferencia es de 4 h, no 6
    assert CAL.is_open(datetime(2026, 3, 16, 14, 30, tzinfo=MADRID))       # 09:30 en NY (EDT)
    assert not CAL.is_open(datetime(2026, 3, 16, 14, 29, tzinfo=MADRID))


def test_next_open_today_tomorrow_and_after_weekend_or_holiday():
    assert CAL.next_open(ny(2026, 10, 1, 4, 56)) == ny(2026, 10, 1, 9, 30)
    assert CAL.next_open(ny(2026, 10, 1, 16, 5)) == ny(2026, 10, 2, 9, 30)
    assert CAL.next_open(ny(2026, 10, 2, 17)) == ny(2026, 10, 5, 9, 30)    # viernes -> lunes
    assert CAL.next_open(ny(2026, 11, 25, 17)) == ny(2026, 11, 27, 9, 30)  # salta el festivo


def test_naive_datetime_is_local_system_time():
    naive = datetime(2026, 10, 1, 12, 0)
    assert CAL.is_open(naive) == CAL.is_open(naive.astimezone(NY))


def test_from_settings_and_validation():
    cal = MarketCalendar.from_settings(MarketSettings(holidays=[date(2026, 12, 25)]))
    assert not cal.is_open(ny(2026, 12, 25, 12)) and cal.open == time(9, 30)
    with pytest.raises(ValueError, match="zona horaria"):
        MarketSettings(timezone="Marte/Olimpo")
    with pytest.raises(ValueError, match="anterior"):
        MarketSettings(open=time(16, 0), close=time(9, 30))


def test_last_close_during_and_after_the_session_weekend_and_holiday():
    assert CAL.last_close(ny(2026, 10, 1, 12)) == ny(2026, 9, 30, 16)      # en sesión: el de ayer
    assert CAL.last_close(ny(2026, 10, 1, 4, 56)) == ny(2026, 9, 30, 16)   # antes de abrir
    assert CAL.last_close(ny(2026, 10, 1, 16, 0)) == ny(2026, 10, 1, 16)   # justo al cerrar: el de hoy
    assert CAL.last_close(ny(2026, 10, 1, 23)) == ny(2026, 10, 1, 16)
    assert CAL.last_close(ny(2026, 10, 5, 8)) == ny(2026, 10, 2, 16)       # lunes antes de abrir: el del viernes
    assert CAL.last_close(ny(2026, 10, 3, 12)) == ny(2026, 10, 2, 16)      # sábado
    assert CAL.last_close(ny(2026, 11, 27, 9)) == ny(2026, 11, 25, 16)     # tras el festivo del 26


def test_delayed_calendar_shifts_open_and_close_by_the_delay():
    cal = MarketCalendar(NY, delay_minutes=15)
    assert not cal.is_open(ny(2026, 10, 1, 9, 44)) and cal.is_open(ny(2026, 10, 1, 9, 45))
    assert cal.is_open(ny(2026, 10, 1, 16, 14)) and not cal.is_open(ny(2026, 10, 1, 16, 15))
    assert cal.last_close(ny(2026, 10, 1, 16, 10)) == ny(2026, 9, 30, 16, 15)   # aún llegan datos del día
    assert cal.last_close(ny(2026, 10, 1, 16, 15)) == ny(2026, 10, 1, 16, 15)
    assert cal.next_open(ny(2026, 10, 1, 4, 0)) == ny(2026, 10, 1, 9, 45)
