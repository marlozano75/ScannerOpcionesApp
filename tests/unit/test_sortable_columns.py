"""Ordenación de columnas de la watchlist y del scanner al pulsar el título."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "src" / "scanner_opciones" / "ui" / "templates" / "sortable.js"
JS_TEST = ROOT / "tests" / "js" / "sortable.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js no está instalado")
def test_sorting_logic_in_node():
    r = subprocess.run(["node", str(JS_TEST), str(JS)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout


def test_sortable_script_is_packaged():
    assert JS.is_file()
    assert "ui/templates/*.js" in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
