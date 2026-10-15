"""Fecha exacta de apertura de las puts vendidas, tomada de las operaciones del propio usuario en Tortuga Trades.

IBKR no da la fecha de apertura de una posición. Una rutina diaria (`scripts/actualizar_aperturas.ps1`) pide al conector de Tortuga las
ejecuciones de apertura de puts vendidas y deja un JSON; la aplicación solo lo lee. Formato (versión 1):

    {
      "version": 1,
      "generated_at": "2026-10-15T13:00:00+02:00",
      "source": "tortuga",
      "puts": [   # ejecuciones de apertura (SELL, put); si un contrato se abrió en varios días, la aplicación usa la primera
        {"date": "2026-10-13", "ticker": "FRO", "expiry": "2026-11-20", "strike": 49.2, "qty": 2}
      ]
    }

Un elemento mal formado se descarta y se anota en `warnings`; un fichero que no es de este formato lanza `OpeningsError`.
Tortuga publica las operaciones con un día de retraso: una put abierta hoy no está todavía y se usa la fecha en que la app la vio.
Comprobación: `python -m scanner_opciones.portfolio.openings fichero.json` (código 0 si es válido)."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Optional

VERSION = 1


def norm_ticker(ticker: str) -> str:
    """BRK B, BRK-B y BRK.B son el mismo valor (IBKR escribe la clase con espacio, la app con guion)."""
    return re.sub(r"[ .\-/]", "", ticker.upper())


class OpeningsError(ValueError):
    """El fichero de aperturas no existe o no tiene el formato esperado."""


@dataclass(frozen=True)
class OpeningsData:
    generated_at: datetime
    opened: dict = field(default_factory=dict)      # (ticker normalizado, vencimiento, strike redondeado) -> fecha de la primera apertura
    warnings: tuple[str, ...] = ()

    def get(self, ticker: str, expiry: date, strike: float) -> Optional[date]:
        return self.opened.get((norm_ticker(ticker), expiry, round(float(strike), 4)))


def parse_openings(raw: object) -> OpeningsData:
    if not isinstance(raw, dict) or raw.get("version") != VERSION or not isinstance(raw.get("puts"), list):
        raise OpeningsError(f"no es un JSON de aperturas de la versión {VERSION}")
    try:
        generated = datetime.fromisoformat(str(raw["generated_at"]))
    except (KeyError, ValueError):
        raise OpeningsError("falta generated_at (fecha ISO)") from None
    opened: dict = {}
    warnings: list[str] = []
    for i, row in enumerate(raw["puts"]):
        try:
            key = (norm_ticker(str(row["ticker"])), date.fromisoformat(str(row["expiry"])), round(float(row["strike"]), 4))
            day = date.fromisoformat(str(row["date"]))
            if not key[0] or key[2] <= 0:
                raise ValueError("ticker o strike vacío")
        except (KeyError, TypeError, ValueError) as exc:
            warnings.append(f"puts[{i}] descartado: {exc}")
            continue
        if key not in opened or day < opened[key]:
            opened[key] = day
    return OpeningsData(generated, opened, tuple(warnings))


def load_openings(path: str | Path) -> OpeningsData:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OpeningsError(f"no se pudo leer {path}: {exc}") from exc
    return parse_openings(raw)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):      # la consola de Windows usa cp1252: los mensajes llevan tildes
        stream.reconfigure(encoding="utf-8")
    if len(argv) != 1:
        print("uso: python -m scanner_opciones.portfolio.openings fichero.json", file=sys.stderr)
        return 2
    try:
        data = load_openings(argv[0])
    except OpeningsError as exc:
        print(f"NO VÁLIDO: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {len(data.opened)} contratos, generado {data.generated_at.isoformat()}")
    for w in data.warnings:
        print(f"aviso: {w}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
