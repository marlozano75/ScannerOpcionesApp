"""Carga de la página de HelloStocks guardada desde el navegador (.html) como fuentes del Universo."""
import json

import pytest

from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.universe.hellostocks_html import missing_strategies
from scanner_opciones.universe.sources import load_sources


def _block(name: str, holdings: int, rows: list[tuple], criteria: list[tuple] | None = None) -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    head = "".join(f"<th>{h}</th>" for h in ("Ticker", "Company", "Sector", "HelloStocks Score 🔒", "Criteria", "ROE"))
    crit = ""
    if criteria is not None:                       # con «Strategy Criteria» abierto, esta tabla va ANTES de la de acciones
        crit = "<table><tr><th>Metric</th><th>Condition</th></tr>" + "".join(
            f"<tr><td>{m}</td><td>{c}</td></tr>" for m, c in criteria) + "</table>"
    return f'<div id="{name}">{name} Holdings: {holdings} {crit}<table><tr>{head}</tr>{body}</table></div>'


def _page(declared: list[str], blocks: str) -> str:
    data = json.dumps({"strategyArray": [{"name": n, "stocks": []} for n in declared]})
    return f'<html><body>{blocks}<script>self.__next_f.push([1,{json.dumps(data)}])</script></body></html>'


@pytest.fixture
def page(tmp_path):
    blocks = (
        _block("Lower Risk (Hello Stocks)", 2, [("ADBE", "Adobe Inc", "Technology", "6.7", "7/7", "62.9%"),
                                                ("PFE", "Pfizer Inc", "Healthcare", "5.0", "6/7", "18.0%")],
               criteria=[("ROE", "&gt; 0.15"), ("Debt to Equity", "&gt;= 0 and  &lt; 1"), ("PE Ratio", "10 &lt; x &lt; 30")])
        + _block("Value Investing (Warren Buffett)", 3, [("ACN", "Accenture plc", "Technology", "5.3", "7/7", "24.9%")])
    )
    path = tmp_path / "HelloStocks.html"
    path.write_text(_page(["Lower Risk (Hello Stocks)", "Value Investing (Warren Buffett)",
                           "Magic Formula Investing (Joel Greenblatt)"], blocks), encoding="utf-8")
    return path


def test_cada_estrategia_es_una_fuente_con_el_nombre_del_libro_antiguo(page):
    sources = load_sources(page, "HelloStocks.html")
    assert [s.name for s in sources] == ["Lower Risk (Hello Stocks)", "Value Investing-Warren Buffett"]
    assert [r.ticker for r in sources[0].table.rows] == ["ADBE", "PFE"]


def test_se_omite_la_columna_de_miembros_y_criteria_sigue_siendo_texto(page):
    table = load_sources(page, "HelloStocks.html")[0].table
    names = [c.name for c in table.columns]
    assert "Fuente" in names and "Criteria" in names
    assert not any("score" in n.lower() for n in names)
    assert "7/7" in {cell.text for cell in table.rows[0].cells}


def test_avisa_de_la_estrategia_declarada_sin_tabla_y_de_las_filas_que_faltan(page):
    avisos = missing_strategies(page)
    assert any("Magic Formula" in a and "no está" in a for a in avisos)
    assert any("Value Investing (Warren Buffett)" in a and "declara 3" in a for a in avisos)
    assert not any("Lower Risk" in a for a in avisos)


def test_html_sin_tablas_da_error_claro(tmp_path):
    path = tmp_path / "vacio.html"
    path.write_text("<html><body>nada</body></html>", encoding="utf-8")
    with pytest.raises(WatchlistError, match="desplegadas"):
        load_sources(path, "vacio.html")


def test_con_strategy_criteria_abierto_las_acciones_siguen_siendo_la_tabla_de_ticker_y_los_criterios_se_leen(page):
    lower, value = load_sources(page, "HelloStocks.html")
    assert [r.ticker for r in lower.table.rows] == ["ADBE", "PFE"]                      # no se confunde con la de criterios
    assert lower.criteria == (("ROE", "> 15 %"), ("Debt to Equity", "≥ 0 y < 1"), ("PE Ratio", "entre 10 y 30"))
    assert value.criteria == ()                                                          # sin tabla de criterios


def test_avisa_de_las_estrategias_sin_criterios(page):
    avisos = missing_strategies(page)
    assert any("Value Investing" in a and "Strategy Criteria" in a for a in avisos)
    assert not any("Lower Risk" in a and "Strategy Criteria" in a for a in avisos)
