"""Piezas compartidas por las rutas de la interfaz: límites y los mensajes agrupados del Universo."""
from __future__ import annotations

import re
from typing import Optional

MAX_FORM_FIELDS = 100_000      # Starlette corta en 1000 campos; las tablas con casillas (Universo) mandan más (una por ticker o por fuente y filtro)
IDEAS_WAIT_SECONDS = 10     # lo que espera la pestaña Comunidad a que terminen las cotizaciones en directo antes de mostrarse con lo que haya


def source_initials(name: str) -> str:
    """Iniciales de una fuente del Universo para las etiquetas de Ideas: «TortugaTrades_Dia» → TTD, «Quality Growth» → QG."""
    words = re.findall(r"[A-Z][a-z0-9]*|[a-z0-9]+|[A-Z]+(?![a-z])", name.replace("_", " "))
    return "".join(w[0] for w in words).upper()[:4] or name[:2].upper()


def _grp(kind: str, title: str, items=(), note: str = "") -> str:
    """Un grupo de un mensaje del Universo: `kind` (ok, del, skip, warn, err), título, elementos (tickers, ficheros) y nota."""
    return "|".join((kind, title.replace("|", "/"), "\t".join(str(i).replace("|", "/").replace("\t", " ") for i in items), note.replace("|", "/")))


def message_groups(message: str) -> list[dict]:
    """Interpreta un mensaje de `_grp` (uno por línea). Una línea sin formato se muestra como aviso informativo."""
    out = []
    for line in (message or "").splitlines():
        parts = line.split("|")
        if len(parts) == 4 and parts[0] in ("ok", "del", "skip", "warn", "err"):
            kind, title, items, note = parts
            out.append({"kind": kind, "title": title, "chips": [i for i in items.split("\t") if i], "note": note})
        elif line.strip():
            out.append({"kind": "err" if line.startswith("Error") else "info", "title": "", "chips": [], "note": line})
    return out


def _load_groups(raw: dict[str, list[str]], loaded, before: dict[str, list[str]]) -> list[str]:
    """Grupos del mensaje de una carga de fichero: por fuente, los tickers nuevos, los quitados (estaban en la fuente
    que se reemplaza y ya no vienen) y los conservados; y, aparte, los descartados por no tener opciones."""
    out = []
    after = {src.name: [r.ticker for r in src.table.rows] for src in loaded}
    for name, tickers in after.items():
        old = before.get(name)
        if old is None:
            out.append(_grp("ok", f"Fuente nueva: {name}", [], f"{len(tickers)} tickers"))
            continue
        fresh = [t for t in tickers if t not in set(old)]
        gone = [t for t in old if t not in set(tickers)]
        kept = len(tickers) - len(fresh)
        out.append(_grp("ok" if fresh else "skip", f"Nuevos en {name}", fresh[:60],
                        f"{len(fresh)} nuevos · {kept} conservados" + (f" · +{len(fresh) - 60} más" if len(fresh) > 60 else "")))
        if gone:
            out.append(_grp("del", f"Quitados de {name}", gone[:60], f"{len(gone)} quitados" if len(gone) > 60 else ""))
    no_opts = sorted({t for n, ts in raw.items() for t in ts} - {t for ts in after.values() for t in ts})
    if no_opts:
        out.append(_grp("warn", "Sin opciones (descartados)", no_opts[:60], f"{len(no_opts)} tickers" + (" · se muestran 60" if len(no_opts) > 60 else "")))
    return out
