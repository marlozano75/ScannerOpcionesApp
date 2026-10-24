"""Guarda a mano el resumen (en español) y las noticias revisadas de una o varias empresas: mandan sobre lo automático en el tooltip del ticker.

Uso: `python -m scanner_opciones.marketdata.company_cli fichero.json [--config config/config.yaml]`, con
`{"HOOD": {"summary": "Plataforma de bolsa...", "news": [{"day": "2026-10-20", "text": "Lanza futuros perpetuos", "source": "Reuters"}]}}`.
`summary` y/o `news` pueden faltar (lo que falte se deja como estaba); `news: []` borra las revisadas. La app puede estar abierta."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from scanner_opciones.config.settings import load_settings
from scanner_opciones.marketdata.company import NewsItem
from scanner_opciones.storage.db import Database
from scanner_opciones.storage.repositories import CompanyNotesRepo


def apply(repo: CompanyNotesRepo, document: dict, today: date) -> int:
    done = 0
    for ticker, entry in document.items():
        summary = entry.get("summary")
        news = None
        if "news" in entry:
            news = [NewsItem.from_json({"day": n["day"], "headline": n.get("text") or n.get("headline"), "source": n.get("source", ""),
                                        "url": n.get("url", "")}) for n in entry["news"]]
        repo.save_curated(ticker.upper(), summary, news, today)
        done += 1
    return done


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file")
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
        db = Database(load_settings(args.config).storage.path)
        count = apply(CompanyNotesRepo(db), document, date.today())
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(f"No válido: {exc}", file=sys.stderr)
        return 1
    print(f"{count} empresas actualizadas")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
