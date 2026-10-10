import pytest
from openpyxl import Workbook

from scanner_opciones.domain.errors import WatchlistError
from scanner_opciones.watchlist.loader import load_watchlist_file
from scanner_opciones.watchlist.parser import parse_text


class TestParseText:
    def test_mixed_separators(self):
        r = parse_text("aapl, msft;NVDA\tko\nPEP  JNJ")
        assert r.tickers == ["AAPL", "MSFT", "NVDA", "KO", "PEP", "JNJ"]
        assert r.rejected == [] and r.duplicates == 0

    def test_dedupe_keeps_order(self):
        r = parse_text("AAPL MSFT aapl AAPL")
        assert r.tickers == ["AAPL", "MSFT"]
        assert r.duplicates == 2

    def test_empty(self):
        assert parse_text("").tickers == []
        assert parse_text("  \n , ; ").tickers == []

    def test_rejects_invalid(self):
        r = parse_text("AAPL 123 $$ TOOLONGTICKERX")
        assert r.tickers == ["AAPL"]
        assert [t for t, _ in r.rejected] == ["123", "$$", "TOOLONGTICKERX"]

    def test_class_shares(self):
        assert parse_text("BRK.B BF-B").tickers == ["BRK.B", "BF-B"]

    def test_header_skipped(self):
        assert parse_text("Ticker\nAAPL\nMSFT").tickers == ["AAPL", "MSFT"]

    def test_header_not_skipped_when_disabled(self):
        assert "TICKER" in parse_text("Ticker AAPL", skip_header=False).tickers


class TestLoadFile:
    def test_txt(self, tmp_path):
        p = tmp_path / "w.txt"
        p.write_text("AAPL\nMSFT\n", encoding="utf-8")
        assert load_watchlist_file(p).tickers == ["AAPL", "MSFT"]

    def test_csv_with_bom_and_header(self, tmp_path):
        p = tmp_path / "w.csv"
        p.write_text("Symbol\nAAPL\nKO\n", encoding="utf-8-sig")
        assert load_watchlist_file(p).tickers == ["AAPL", "KO"]

    def test_latin1_fallback(self, tmp_path):
        p = tmp_path / "w.txt"
        p.write_bytes("AAPL\nñ\n".encode("latin-1"))
        r = load_watchlist_file(p)
        assert r.tickers == ["AAPL"] and len(r.rejected) == 1

    def _xlsx(self, tmp_path, rows):
        wb = Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        p = tmp_path / "w.xlsx"
        wb.save(p)
        return p

    def test_xlsx_with_header(self, tmp_path):
        p = self._xlsx(tmp_path, [["Ticker", "Nota"], ["aapl", "x"], [None], ["MSFT", "y"]])
        assert load_watchlist_file(p).tickers == ["AAPL", "MSFT"]

    def test_xlsx_without_header(self, tmp_path):
        p = self._xlsx(tmp_path, [["AAPL"], ["MSFT"]])
        assert load_watchlist_file(p).tickers == ["AAPL", "MSFT"]

    def test_xlsx_corrupt(self, tmp_path):
        p = tmp_path / "bad.xlsx"
        p.write_text("no soy un excel")
        with pytest.raises(WatchlistError, match="Excel"):
            load_watchlist_file(p)

    def test_missing_file(self, tmp_path):
        with pytest.raises(WatchlistError, match="No existe"):
            load_watchlist_file(tmp_path / "nada.txt")

    def test_unsupported_extension(self, tmp_path):
        p = tmp_path / "w.pdf"
        p.write_text("x")
        with pytest.raises(WatchlistError, match="Formato"):
            load_watchlist_file(p)


def test_quita_el_dolar_inicial_del_ticker():
    res = parse_text("$AAPL, $msft KO $$NVDA")
    assert res.tickers == ["AAPL", "MSFT", "KO", "NVDA"] and res.rejected == []
