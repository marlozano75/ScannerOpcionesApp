"""Copia en disco de las velas ya limpias, para el arranque en frío (2026-11-13).

Leer los ~2,16 millones de filas de `daily_bars` y limpiarlas cuesta ~5,8 s en cada arranque (3 s son solo traerlas de SQLite a Python; ver
`docs/rendimiento-peticiones.md`). Esta copia guarda, junto a la base de datos (`data/app.bars`), lo que deja `BarRepo._load_all`: los cierres
limpios de las velas OFICIALES y las velas oficiales completas, por ticker y en arrays compactos. Reconstruirlos desde aquí cuesta ~1 s.

Validez: la copia lleva una etiqueta (`fingerprint`) con el formato, la versión del algoritmo de limpieza (`bar_cleaning.py`), la versión del
esquema (`PRAGMA user_version`), el contador de escrituras oficiales de velas (`meta.bars_generation`, que `BarRepo` sube en la misma
transacción que cada escritura) y el número de filas oficiales. Si algo no coincide, se ignora y se vuelve a leer la base. Los cierres
provisionales del día no están en la copia: cambian en cada refresco y `BarRepo` los superpone al cargar.

Formato `marshal` (no `pickle`: cargarlo no ejecuta código): primero la etiqueta y después los datos, para comprobar si vale sin leer los ~100 MB.
Se escribe de forma atómica (temporal propio de cada hilo + `os.replace`) y de uno en uno (`_SAVE_LOCK`)."""
from __future__ import annotations

import gc
import logging
import marshal
import os
import threading
import zlib
from array import array
from datetime import date
from pathlib import Path
from typing import Optional

from scanner_opciones.marketdata import bar_cleaning

log = logging.getLogger(__name__)

FORMAT = 1            # súbelo si cambia lo que se guarda o cómo lo filtra `BarRepo` (velas oficiales con máximo y mínimo)
GENERATION_KEY = "bars_generation"
BUMP_SQL = (f"INSERT INTO meta (key, value) VALUES ('{GENERATION_KEY}', '1') "
            "ON CONFLICT(key) DO UPDATE SET value = CAST(value AS INTEGER) + 1")
_CLEANING = zlib.crc32(Path(bar_cleaning.__file__).read_bytes())      # otra limpieza = otros cierres: la copia deja de valer
_NAN = float("nan")
_SAVE_LOCK = threading.Lock()

Closes = dict[str, list[tuple[date, float]]]
Candles = dict[str, list[tuple]]


def path_for(db_path: str) -> Optional[Path]:
    """Fichero de la copia junto a la base (`data/app.db` → `data/app.bars`); None para una base en memoria."""
    if db_path == ":memory:":
        return None
    return Path(db_path).with_suffix(".bars")


def fingerprint(conn) -> tuple:
    """Etiqueta de los datos oficiales actuales. Hay que leerla ANTES que las velas: una escritura entre medias deja la copia con una etiqueta
    vieja (se descartará), nunca con una etiqueta nueva sobre datos viejos."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (GENERATION_KEY,)).fetchone()
    generation = int(row[0]) if row else 0
    total = conn.execute("SELECT COUNT(*) FROM daily_bars").fetchone()[0]
    provisional = conn.execute("SELECT COUNT(*) FROM daily_bars WHERE provisional = 1").fetchone()[0]
    return (FORMAT, _CLEANING, conn.execute("PRAGMA user_version").fetchone()[0], generation, total - provisional)


def _pack(closes: Closes, candles: Candles) -> dict:
    out = {}
    for ticker, bars in closes.items():
        rows = candles.get(ticker, ())
        out[ticker] = (array("i", [d.toordinal() for d, _ in bars]).tobytes(), array("d", [v for _, v in bars]).tobytes(),
                       array("i", [r[0].toordinal() for r in rows]).tobytes(),
                       array("d", [_NAN if r[1] is None else r[1] for r in rows]).tobytes(),
                       array("d", [r[2] for r in rows]).tobytes(), array("d", [r[3] for r in rows]).tobytes(),
                       array("d", [r[4] for r in rows]).tobytes())
    return out


def stored_tag(path: Optional[Path]) -> Optional[tuple]:
    """Etiqueta de la copia guardada (solo la cabecera), o None si no hay o no se puede leer."""
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            tag = marshal.load(f)
        return tag if isinstance(tag, tuple) else None
    except Exception:
        return None


def save(path: Path, tag: tuple, closes: Closes, candles: Candles) -> None:
    """Escribe la copia (atómica). `closes`: cierres limpios de las velas oficiales; `candles`: velas oficiales (día, apertura, máximo,
    mínimo, cierre). No reescribe una copia igual. Si dos guardados acaban en otro orden y queda el más viejo, su etiqueta ya no coincide y
    `BarRepo.refresh_snapshot` la rehace en el siguiente precalentado. Nunca falla: es optativa."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with _SAVE_LOCK:
            current = stored_tag(path)
            if current == tag:
                return
            with tmp.open("wb") as f:
                marshal.dump(tag, f)
                marshal.dump(_pack(closes, candles), f)
            os.replace(tmp, path)
    except Exception:      # disco lleno, permisos, base movida...: la próxima carga leerá la base
        log.warning("No se pudo guardar la copia de las velas en %s", path, exc_info=True)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def save_in_background(path: Path, tag: tuple, closes: Closes, candles: Candles) -> threading.Thread:
    """Guarda en otro hilo (~1 s de empaquetar y escribir). Copia las listas antes: `BarRepo._put` añade cierres a las de su caché."""
    closes = {t: list(v) for t, v in closes.items()}
    thread = threading.Thread(target=save, args=(path, tag, closes, candles), name="bars-snapshot", daemon=True)
    thread.start()
    return thread


def load(path: Optional[Path], tag: tuple) -> Optional[tuple[Closes, Candles]]:
    """(cierres limpios oficiales, velas oficiales) si la copia existe y su etiqueta coincide; si no, None.

    Con el recolector de basura en marcha, crear los ~4 millones de tuplas costaba ~3,7 s; pausado, ~0,9 s (medido 2026-11-13): solo son
    tuplas de números y fechas, sin ciclos que recoger."""
    if path is None or not path.is_file():
        return None
    try:
        with path.open("rb") as f:
            if marshal.load(f) != tag:
                return None
            packed = marshal.load(f)
        was_enabled = gc.isenabled()
        gc.disable()
        try:
            return _unpack(packed)
        finally:
            if was_enabled:
                gc.enable()
    except Exception:      # fichero truncado o de otra versión de Python: se ignora
        log.warning("Copia de las velas ilegible (%s): se leen de la base", path, exc_info=True)
        return None


def _unpack(packed: dict) -> tuple[Closes, Candles]:
    days: dict[int, date] = {}
    closes: Closes = {}
    candles: Candles = {}
    for ticker, parts in packed.items():
        cols = []
        for i, raw in enumerate(parts):
            col = array("i" if i in (0, 2) else "d")
            col.frombytes(raw)
            cols.append(col)
        for o in cols[0]:
            if o not in days:
                days[o] = date.fromordinal(o)
        for o in cols[2]:
            if o not in days:
                days[o] = date.fromordinal(o)
        get = days.__getitem__
        closes[ticker] = list(zip(map(get, cols[0]), cols[1]))
        if len(cols[2]):
            opens = [None if v != v else v for v in cols[3]]      # NaN = apertura no guardada
            candles[ticker] = list(zip(map(get, cols[2]), opens, cols[4], cols[5], cols[6]))
    return closes, candles
