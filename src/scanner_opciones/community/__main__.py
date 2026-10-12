"""Comprueba un JSON de la comunidad: `python -m scanner_opciones.community fichero.json` (código 0 si es válido).
La rutina diaria lo usa para no sustituir el bueno por uno mal formado."""
from __future__ import annotations

import sys

from scanner_opciones.community.data import CommunityError, load_community


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):      # la consola de Windows usa cp1252: los mensajes llevan tildes
        stream.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        print("uso: python -m scanner_opciones.community fichero.json", file=sys.stderr)
        return 2
    try:
        data = load_community(argv[0])
    except CommunityError as exc:
        print(f"NO VÁLIDO: {exc}", file=sys.stderr)
        return 1
    if not data.tickers:
        print("NO VÁLIDO: no hay ningún ticker", file=sys.stderr)
        return 1
    print(f"OK: {len(data.tickers)} tickers, {len(data.ideas)} ideas, generado {data.generated_at.isoformat()}")
    for w in data.warnings:
        print(f"aviso: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
