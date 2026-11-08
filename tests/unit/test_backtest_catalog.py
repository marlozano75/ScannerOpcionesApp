"""Catálogo de la pestaña «Backtest»: el fichero real es válido y el formato rechaza lo mal formado."""
import copy
import json

import pytest

from scanner_opciones.backtest import catalog


def test_the_shipped_results_are_valid_and_cover_every_objective():
    cat = catalog.load()
    assert len(cat.rounds) >= 8 and cat.conclusions and cat.methodology
    assert {r.objective for r in cat.rounds} == set(catalog.OBJECTIVES)
    assert len({r.id for r in cat.rounds}) == len(cat.rounds)
    assert all(r.tables for r in cat.rounds)


def test_first_number_reads_the_spanish_formats():
    f = catalog.first_number
    assert f("12,5 %") == 12.5 and f("+0,8 %") == 0.8 and f("−0,1 a +0,6 %") == -0.1
    assert f("106.000") == 106000 and f("1.011") == 1011 and f("3,6 / 3,7") == 3.6 and f("**47 %**") == 47
    assert f("19-20 %") == 19 and f("Keltner") is None and f("—") is None


def test_cells_are_escaped_and_only_bold_and_code_are_interpreted():
    html = str(catalog.cell_html("**a** <script>x</script> `y`"))
    assert "<strong>a</strong>" in html and "<code>y</code>" in html and "<script>" not in html


def _base():
    return copy.deepcopy(json.loads(catalog.RESULTS_PATH.read_text(encoding="utf-8")))


def test_a_row_with_the_wrong_number_of_cells_is_rejected():
    data = _base()
    data["rondas"][0]["tablas"][0]["filas"][0].append("sobra")
    with pytest.raises(catalog.CatalogError, match="celdas"):
        catalog.parse(data)


def test_unknown_objective_verdict_and_repeated_id_are_rejected():
    for key, value, text in (("objetivo", "otro", "objetivo"), ("veredicto", "genial", "veredicto")):
        data = _base()
        data["rondas"][0][key] = value
        with pytest.raises(catalog.CatalogError, match=text):
            catalog.parse(data)
    data = _base()
    data["rondas"][1]["id"] = data["rondas"][0]["id"]
    with pytest.raises(catalog.CatalogError, match="repetido"):
        catalog.parse(data)


def test_a_bar_column_outside_the_table_is_rejected():
    data = _base()
    data["rondas"][0]["tablas"][0]["barra"] = {"col": 99, "max": 10}
    with pytest.raises(catalog.CatalogError, match="barra"):
        catalog.parse(data)
