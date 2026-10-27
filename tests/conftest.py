"""Los tests no deben tocar `data/` real: `Settings()` usa rutas relativas (`data/app.db`, `data/universe/`),
así que cada test corre en un directorio temporal."""
import pytest


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


@pytest.fixture(autouse=True)
def _no_auto_instruments(request, monkeypatch):
    """Los instrumentos propios (ESTX50) se añaden solos a la Watchlist; en los demás tests ensuciarían sus listas. Se pide con
    `@pytest.mark.instruments`."""
    if request.node.get_closest_marker("instruments") is None:
        from scanner_opciones.app.service import AppService
        monkeypatch.setattr(AppService, "ensure_instruments", lambda self: None)


@pytest.fixture(autouse=True)
def _no_put_floor(request, monkeypatch):
    """Los datos de calidad de los tests casi nunca existen: sin esto la calidad mínima de las puts (`scanner.quality.put_floor`) las bloquearía
    todas. Los tests de ese filtro lo piden con `@pytest.mark.put_floor`."""
    if request.node.get_closest_marker("put_floor") is None:
        from scanner_opciones.app.service import AppService
        monkeypatch.setattr(AppService, "put_floor_reasons", lambda self, criteria=None: {})
