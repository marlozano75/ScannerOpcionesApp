from datetime import date, timedelta

from scanner_opciones.marketdata.bar_cleaning import clean_bars


def _rows(values, high_low=None):
    d0 = date(2025, 1, 1)
    return [(d0 + timedelta(days=i), v, *(high_low.get(i, (None, None)) if high_low else (None, None))) for i, v in enumerate(values)]


def test_pico_aislado_del_dia_del_split_se_descarta():
    out = clean_bars(_rows([115, 116, 1112, 110, 114]))
    assert [c for _, c in out] == [115, 116, 110, 114]


def test_cierre_fuera_del_rango_del_dia_se_descarta():
    out = clean_bars(_rows([10, 10.5, 29.6, 10.4], {2: (10.7, 10.0)}))
    assert [c for _, c in out] == [10, 10.5, 10.4]


def test_split_inverso_sin_ajustar_reescala_lo_anterior():
    out = clean_bars(_rows([10, 10, 10, 30, 30, 30]))
    assert [round(c) for _, c in out] == [30] * 6


def test_movimientos_normales_no_se_tocan():
    vals = [100, 103, 99, 104, 108, 102]
    assert [c for _, c in clean_bars(_rows(vals))] == vals
