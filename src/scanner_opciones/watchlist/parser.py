"""Parseo de watchlists a partir de texto libre (funciones puras)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_SPLIT = re.compile(r"[,;\s]+")
_TICKER = re.compile(r"^[A-Z][A-Z0-9]{0,9}([.\-][A-Z0-9]{1,2})?$")
HEADER_NAMES = {"TICKER", "TICKERS", "SYMBOL", "SYMBOLS", "SIMBOLO", "SÍMBOLO", "SIMBOLOS"}


@dataclass(frozen=True)
class ParseResult:
    tickers: list[str] = field(default_factory=list)
    rejected: list[tuple[str, str]] = field(default_factory=list)  # (token, motivo)
    duplicates: int = 0


def parse_tokens(tokens: list[str]) -> ParseResult:
    """Normaliza (mayúsculas, sin el «$» inicial de «$AAPL»), valida formato y deduplica conservando el orden."""
    seen: set[str] = set()
    tickers: list[str] = []
    rejected: list[tuple[str, str]] = []
    duplicates = 0
    for raw in tokens:
        if not raw.strip():
            continue
        tok = raw.strip().lstrip("$").strip().upper()
        if not _TICKER.match(tok):
            rejected.append((raw.strip(), "formato de ticker no válido"))
        elif tok in seen:
            duplicates += 1
        else:
            seen.add(tok)
            tickers.append(tok)
    return ParseResult(tickers, rejected, duplicates)


def parse_text(text: str, skip_header: bool = True) -> ParseResult:
    """Tickers separados por comas, punto y coma, espacios, tabuladores o saltos de línea."""
    tokens = [t for t in _SPLIT.split(text or "") if t]
    if skip_header and tokens and tokens[0].upper() in HEADER_NAMES:
        tokens = tokens[1:]
    return parse_tokens(tokens)
