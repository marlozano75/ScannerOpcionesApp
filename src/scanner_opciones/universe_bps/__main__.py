"""Universo para bull put spreads (lo usa `scripts/actualizar_universo_bps.ps1`).

  python -m scanner_opciones.universe_bps validar fichero.json            código 0 si el universo es válido
  python -m scanner_opciones.universe_bps construir radar.json salida.json [config.yaml]
      Mezcla la lista inicial y el historial diario con las filas del radar y escribe el universo en `salida.json`
      (solo si es válido; el historial se actualiza en el mismo paso).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from scanner_opciones.config.settings import load_settings
from scanner_opciones.universe_bps.builder import parse_radar, parse_seed, update, warnings
from scanner_opciones.universe_bps.data import UniverseBpsError, load_universe, parse_universe

DEFAULT_CONFIG = "config/config.yaml"
MIN_RADAR_ROWS = 15      # con VRP > 0, liquidez y precio el radar da ~40 valores: menos filas es una respuesta recortada y haría salir a medio universo


def _read(path: "str | Path"):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UniverseBpsError(f"no se pudo leer {path}: {exc}") from exc


def build(radar_path: str, out_path: str, config: str = DEFAULT_CONFIG, now: Optional[datetime] = None, cfg=None) -> str:
    cfg = cfg or load_settings(config).spread_universe
    now = now or datetime.now().astimezone()
    seed, excluded = parse_seed(_read(cfg.seed_path))
    radar = parse_radar(_read(radar_path))
    if len(radar) < MIN_RADAR_ROWS:
        raise UniverseBpsError(f"el radar trae solo {len(radar)} filas (mínimo {MIN_RADAR_ROWS}); respuesta incompleta")
    hist_path = Path(cfg.history_path)
    history = _read(hist_path) if hist_path.exists() else {}
    history, members = update(history, seed, excluded, radar, now.date(), cfg)
    doc = {"version": 1, "generated_at": now.isoformat(timespec="seconds"), "warnings": warnings(members, cfg),
           "members": [{"ticker": m.ticker, "kind": m.kind, "sector": m.sector, "level": m.level, "radar": m.radar, "note": m.note}
                       for m in members]}
    universe = parse_universe(doc)                      # lo que se escribe tiene que ser válido
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(out)
    hist_path.parent.mkdir(parents=True, exist_ok=True)
    hist_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{len(universe.members)} miembros ({len(universe.active())} activos)"


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):      # la consola de Windows usa cp1252: los mensajes llevan tildes
        stream.reconfigure(encoding="utf-8")
    try:
        if len(argv) == 2 and argv[0] == "validar":
            u = load_universe(argv[1])
            print(f"OK: {len(u.members)} miembros, generado {u.generated_at.isoformat()}")
            return 0
        if len(argv) in (3, 4) and argv[0] == "construir":
            print("OK: " + build(argv[1], argv[2], *argv[3:]))
            return 0
    except UniverseBpsError as exc:
        print(f"NO VÁLIDO: {exc}", file=sys.stderr)
        return 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
