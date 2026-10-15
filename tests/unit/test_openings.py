"""Fechas exactas de apertura de las puts vendidas (JSON de la rutina de aperturas de Tortuga)."""
import json
from datetime import date

import pytest

from scanner_opciones.portfolio.openings import OpeningsError, load_openings, norm_ticker, parse_openings


def doc(puts, **over):
    return {"version": 1, "generated_at": "2026-10-15T13:00:00+02:00", "source": "tortuga", "puts": puts, **over}


def test_the_first_opening_date_of_a_contract_wins_and_tickers_are_normalised():
    data = parse_openings(doc([
        {"date": "2026-10-13", "ticker": "FRO", "expiry": "2026-11-20", "strike": 49.2, "qty": 2},
        {"date": "2026-10-01", "ticker": "BRK-B", "expiry": "2026-11-20", "strike": 400, "qty": 1},
        {"date": "2026-10-05", "ticker": "BRK.B", "expiry": "2026-11-20", "strike": 400.0, "qty": 1},     # mismo contrato, más tarde
    ]))
    assert data.get("FRO", date(2026, 11, 20), 49.2) == date(2026, 10, 13)
    assert data.get("BRK B", date(2026, 11, 20), 400) == date(2026, 10, 1)                              # IBKR escribe BRK B
    assert data.get("FRO", date(2026, 11, 20), 50) is None and norm_ticker("brk b") == "BRKB"


def test_malformed_rows_are_dropped_with_a_warning_and_a_bad_document_is_rejected(tmp_path):
    data = parse_openings(doc([{"date": "2026-10-13", "ticker": "VZ", "expiry": "2026-11-20", "strike": 40},
                               {"date": "no", "ticker": "X", "expiry": "2026-11-20", "strike": 1}, {"ticker": "Y"}]))
    assert len(data.opened) == 1 and len(data.warnings) == 2
    for bad in ({"version": 2, "puts": []}, doc([], generated_at="ayer"), {"version": 1}, []):
        with pytest.raises(OpeningsError):
            parse_openings(bad)
    p = tmp_path / "a.json"
    p.write_text(json.dumps(doc([])), encoding="utf-8")
    assert load_openings(p).opened == {}
    with pytest.raises(OpeningsError):
        load_openings(tmp_path / "no-existe.json")
