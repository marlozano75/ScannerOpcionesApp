"""Los jobs en su propio hilo y bucle de eventos (`jobs/loop_thread.py`, `ui/loops.py`): la interfaz no espera a sus tramos síncronos,
lee por una conexión propia y todo lo que no es una lectura se ejecuta en el bucle de los jobs."""
import threading
import time

import pytest
from fastapi.testclient import TestClient

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.domain.models import AccountSummary
from scanner_opciones.jobs.loop_thread import JobsLoop
from scanner_opciones.storage.db import Database
from scanner_opciones.ui.web import create_app
from tests.integration.test_service import NOW, FixedMarket, seed_market


def wait_idle(svc: AppService) -> None:
    """Espera, desde el hilo del test, a que acaben las tareas de fondo del bucle de los jobs."""
    import asyncio
    asyncio.run_coroutine_threadsafe(svc.wait_idle(), svc.jobs_loop).result(30)


@pytest.fixture
def split(tmp_path):
    gw = FakeGateway(account=AccountSummary("DU1", net_liquidation=100_000, excess_liquidity=45_000))
    seed_market(gw)
    db = Database(tmp_path / "app.db")      # en fichero: una base en memoria no admite la segunda conexión
    svc = AppService(gw, db, Settings(), lambda: NOW, market=FixedMarket(True))
    svc.watchlist.add(["AAPL"], NOW)
    app = create_app(svc, lambda mode: FakeGateway(), on_startup=svc.start, on_shutdown=svc.stop, jobs=JobsLoop())
    seen: dict = {}

    @app.get("/_hilo")
    async def where():      # dónde se atiende una lectura
        return {"thread": threading.current_thread().name, "reader": db.conn is db._reader}

    @app.post("/_hilo")
    async def where_post():
        return {"thread": threading.current_thread().name, "reader": db.conn is db._reader}

    @app.get("/_carrera")
    async def race():       # la primera vez falla como si los jobs hubieran cambiado un diccionario mientras se recorría
        seen["race"] = seen.get("race", 0) + 1
        if seen["race"] == 1:
            raise RuntimeError("dictionary changed size during iteration")
        return {"ok": seen["race"]}

    with TestClient(app) as client:
        wait_idle(svc)
        yield client, svc, db
    db.close()


def test_lecturas_en_la_interfaz_y_escrituras_en_los_jobs(split):
    client, svc, db = split
    assert svc.jobs_loop is not None and db._reader is not None
    read, write = client.get("/_hilo").json(), client.post("/_hilo").json()
    assert read["thread"] != "jobs" and read["reader"] is True       # GET: hilo de la interfaz, conexión de lectura propia
    assert write == {"thread": "jobs", "reader": False}             # POST: hilo de los jobs, conexión de siempre


def test_las_páginas_no_esperan_a_un_tramo_síncrono_de_los_jobs(split):
    client, svc, _ = split
    client.get("/data-version")       # la primera petición paga la preparación del cliente
    busy = threading.Event()

    def block() -> None:
        busy.set()
        time.sleep(1.5)

    svc.jobs_loop.call_soon_threadsafe(block)
    assert busy.wait(5)
    start = time.perf_counter()
    assert client.get("/data-version").status_code == 200
    assert client.get("/watchlist").status_code == 200
    assert time.perf_counter() - start < 1.0      # con un solo bucle habrían esperado el segundo y medio entero


def test_páginas_y_acciones_funcionan_con_los_jobs_en_su_hilo(split):
    client, svc, _ = split
    for path in ("/", "/watchlist", "/scanner", "/universe", "/exits", "/scanner/config", "/historico", "/backtest", "/comunidad", "/marketradar"):
        assert client.get(path).status_code == 200, path
    before = svc.state.data_version
    assert client.post("/refresh", follow_redirects=False).status_code == 303      # lanza el refresco en el bucle de los jobs
    wait_idle(svc)
    assert svc.state.data_version > before and svc.state.connected
    assert client.get("/scanner").status_code == 200


def test_lo_que_escribe_un_hilo_lo_ve_el_otro_e_invalida_las_cachés(split):
    client, svc, db = split
    changes = db.data_changes
    assert set(svc.ticker_info.all()) == set(client.portal.call(_infos, svc))
    client.portal.call(_add_ticker, svc)                    # escribe el hilo de la interfaz, por su conexión
    assert db.data_changes > changes                        # las escrituras de las dos conexiones cuentan
    assert "MSFT" in svc.watchlist.list()                   # y este hilo (conexión principal) la ve


async def _infos(svc):
    return svc.ticker_info.all()


async def _add_ticker(svc):
    svc.watchlist.add(["MSFT"], NOW)


def test_una_lectura_que_choca_con_los_jobs_se_repite(split):
    client, _, _ = split
    assert client.get("/_carrera").json() == {"ok": 2}


def test_launch_desde_la_interfaz_corre_en_el_bucle_de_los_jobs(split):
    client, svc, _ = split
    ran: list[str] = []

    async def job() -> None:
        ran.append(threading.current_thread().name)

    async def from_web() -> None:
        task = svc.launch(job())          # devuelve un futuro de `concurrent.futures`
        await svc.wait_task(task, 5)
        assert await svc.on_jobs(_thread_name()) == "jobs"

    client.portal.call(from_web)
    assert ran == ["jobs"]


async def _thread_name() -> str:
    return threading.current_thread().name


def test_sin_hilo_de_jobs_todo_sigue_en_un_bucle():
    db = Database(":memory:")
    assert db.split_reader() is False and db.conn is db._main      # una base en memoria no se separa
