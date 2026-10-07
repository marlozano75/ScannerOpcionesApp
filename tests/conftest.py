"""Los tests no deben tocar `data/` real: `Settings()` usa rutas relativas (`data/app.db`, `data/universe/`),
así que cada test corre en un directorio temporal."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
