import pytest
from openpyxl import Workbook

from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.rankedstocks.filters import apply_filters, parse_filters
from scanner_opciones.rankedstocks.loader import (
    CHOICE, NUMBER, TEXT, build_table, clean_header, clean_ticker, load_table, parse_number,
)

HEADER = ["Símbolo", "Empresa", "Bolsa", "País", "Capitalización", "Precio", "RS ↓", "Al"]
ROWS = [
    ["🇺🇸DK", "Delek US Holdings", "NYSE", "US", "$4.4B", "$71.37", "95.6", "Oct 1, 2026"],
    ["🇺🇸PARR", "Par Pacific", "NYSE", "US", "$4.1B", "$81.62", "94.8", "Oct 1, 2026"],
    ["🇺🇸GCT", "GigaCloud", "NASDAQ", "US", "$2.0B", "$52.62", "90.8", "Oct 1, 2026"],
    ["🇸🇬ECO", "Okeanis Eco", "NYSE", "SG", "$517.1M", "$9.25", "85.6", "Oct 1, 2026"],
]


def table(rows=None, header=None):
    return build_table("t.xlsx", [header or HEADER] + (ROWS if rows is None else rows))


class QP(dict):
    """Imita starlette QueryParams: get / getlist."""

    def getlist(self, key):
        v = self.get(key)
        return v if isinstance(v, list) else ([v] if v else [])


@pytest.mark.parametrize("text, expected", [
    ("$4.4B", 4.4e9), ("$517.1M", 517.1e6), ("$71.37", 71.37), ("95.6", 95.6), ("1,234", 1234.0),
    ("12%", 12.0), ("500m", 500e6), ("2K", 2000.0), ("-3.5", -3.5), ("1.2T", 1.2e12),
    ("", None), ("—", None), ("abc", None), ("Oct 1, 2026", None), ("$", None), ("1.2.3", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_clean_ticker_removes_flag_and_header_arrows():
    assert clean_ticker("🇺🇸DK") == "DK" and clean_ticker(" aapl ") == "AAPL"
    assert clean_ticker("🇺🇸BRK.B") == "BRK" and clean_ticker("PBR-A") == "PBR" and clean_ticker("🇺🇸a-b.c") == "A"
    assert clean_header("RS ↓", 0) == "RS" and clean_header("Precio", 1) == "Precio" and clean_header(None, 3) == "Columna 4"


def test_columns_are_inferred_from_the_data():
    kinds = {c.name: c.kind for c in table().columns}
    assert kinds == {"Símbolo": TEXT, "Empresa": TEXT, "Bolsa": CHOICE, "País": CHOICE,
                     "Capitalización": NUMBER, "Precio": NUMBER, "RS": NUMBER, "Al": CHOICE}
    bolsa = next(c for c in table().columns if c.name == "Bolsa")
    assert bolsa.choices == ("NASDAQ", "NYSE")


def test_rows_keep_original_text_and_numeric_value():
    t = table()
    assert [r.ticker for r in t.rows] == ["DK", "PARR", "GCT", "ECO"]
    cap = t.rows[0].cells[4]
    assert cap.text == "$4.4B" and cap.value == 4.4e9
    assert t.rows[0].cells[1].value is None                       # columna de texto: sin valor numérico


def test_many_distinct_values_in_a_column_are_text_not_choice():
    rows = [[f"S{i}", f"Empresa {i}", "NYSE", "US", "$1B", "$1", "1", "Oct 1, 2026"] for i in range(30)]
    kinds = {c.name: c.kind for c in table(rows).columns}
    assert kinds["Empresa"] == TEXT and kinds["Bolsa"] == CHOICE


def test_load_errors():
    with pytest.raises(WatchlistError, match="sin datos|filas de datos"):
        build_table("t.xlsx", [HEADER])
    with pytest.raises(WatchlistError, match="Símbolo"):
        build_table("t.xlsx", [["Empresa", "Precio"], ["X", "$1"]])
    with pytest.raises(WatchlistError, match="No existe"):
        load_table("no_existe.xlsx")
    with pytest.raises(WatchlistError, match="Formato no soportado"):
        load_table(__file__)


def test_load_table_reads_a_real_xlsx(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(HEADER)
    for r in ROWS:
        ws.append(r)
    ws.append([None] * len(HEADER))                                # fila vacía: se ignora
    path = tmp_path / "RankedStocks_2026.10.01.xlsx"
    wb.save(path)
    t = load_table(path)
    assert len(t.rows) == 4 and t.columns[6].name == "RS" and t.symbol_index == 0


def test_numeric_cells_stored_as_numbers_in_the_xlsx(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["Symbol", "Precio"])
    ws.append(["AAPL", 190.5])
    ws.append(["KO", 60])
    path = tmp_path / "n.xlsx"
    wb.save(path)
    t = load_table(path)
    assert [c.text for c in (t.rows[0].cells[1], t.rows[1].cells[1])] == ["190.5", "60"]
    assert t.columns[1].kind == NUMBER and t.rows[1].cells[1].value == 60.0


# ---- filtros ----
def run(**qp):
    t = table()
    return [r.ticker for r in apply_filters(t, parse_filters(t, QP(qp)))]


def test_no_filters_returns_everything():
    assert run() == ["DK", "PARR", "GCT", "ECO"]
    assert not parse_filters(table(), QP()).active


def test_text_filter_is_case_insensitive_contains():
    assert run(t1="pac") == ["PARR"] and run(t0="dk") == ["DK"]


def test_choice_filter_one_or_several_values():
    assert run(c2=["NASDAQ"]) == ["GCT"]
    assert run(c3=["SG"]) == ["ECO"]
    assert run(c2=["NASDAQ", "NYSE"], c3=["US"]) == ["DK", "PARR", "GCT"]
    assert run(c2=["INEXISTENTE"]) == ["DK", "PARR", "GCT", "ECO"]   # un valor desconocido no filtra


def test_numeric_ranges_accept_money_and_suffixes():
    assert run(min5="$60", max5="85") == ["DK", "PARR"]
    assert run(min4="4B") == ["DK", "PARR"]
    assert run(max4="500M") == []
    assert run(max4="600M") == ["ECO"]
    assert run(min6="94") == ["DK", "PARR"]


def test_filters_combine_with_and():
    assert run(c2=["NYSE"], max5="75", min6="90") == ["DK"]


@pytest.mark.parametrize("qp", [{"min5": "abc"}, {"max4": "5X"}, {"min5": "80", "max5": "10"}])
def test_invalid_numbers_raise(qp):
    t = table()
    with pytest.raises(ValueError):
        parse_filters(t, QP(qp))


def test_empty_numeric_cells_do_not_match_a_range():
    t = table(rows=[["A", "x", "NYSE", "US", "$1B", "", "1", "d"], ["B", "y", "NYSE", "US", "$1B", "$5", "1", "d"]])
    assert [r.ticker for r in apply_filters(t, parse_filters(t, QP({"min5": "1"})))] == ["B"]
