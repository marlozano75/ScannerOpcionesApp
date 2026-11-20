"""Apagado limpio: las tareas de fondo se cancelan antes de cerrar la base, y una segunda instancia no arranca."""
import asyncio
import socket

from scanner_opciones.app.service import AppService
from scanner_opciones.broker.fake_gateway import FakeGateway
from scanner_opciones.config.settings import Settings
from scanner_opciones.main import main, port_in_use
from scanner_opciones.storage.db import Database


async def test_stop_cancela_las_tareas_de_fondo():
    svc = AppService(FakeGateway(), Database(), Settings())
    cancelled = []

    async def eterna():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(True)
            svc.launch(asyncio.sleep(3600))      # una tarea lanzada al cancelarse también se cancela
            raise

    task = svc.launch(eterna())
    await asyncio.sleep(0)
    await svc.stop(timeout=2)
    assert task.cancelled() and cancelled
    assert not [t for t in svc._background if not t.done()]


def test_segunda_instancia_no_arranca(capsys):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert port_in_use("127.0.0.1", port)
        assert main(["--port", str(port), "--config", "no-existe.yaml"]) == 1
    assert "Ya hay una instancia" in capsys.readouterr().err
    assert not port_in_use("127.0.0.1", port)
