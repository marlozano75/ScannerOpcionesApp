"""Los tests no deben tocar `data/` real: `Settings()` usa rutas relativas (`data/app.db`, `data/universe/`),
así que cada test corre en un directorio temporal."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_put_floor(request, monkeypatch):
    """Los datos de calidad de los tests casi nunca existen: sin esto la calidad mínima de las puts (`scanner.quality.put_floor`) las bloquearía
    todas. Los tests de ese filtro lo piden con `@pytest.mark.put_floor`."""
    if request.node.get_closest_marker("put_floor") is None:
        from scanner_opciones.app.service import AppService
        monkeypatch.setattr(AppService, "put_floor_reasons", lambda self, criteria=None: {})
