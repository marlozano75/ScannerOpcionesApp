import socket

from scanner_opciones.broker import probe
from scanner_opciones.config.settings import IbkrSettings, Ports
from scanner_opciones.domain.enums import AccountMode


def _settings(live, paper, mode=AccountMode.PAPER):
    return IbkrSettings(ports=Ports(live=live, paper=paper), mode=mode)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _listener():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    return s, s.getsockname()[1]


def test_solo_responde_el_puerto_real():
    srv, live = _listener()
    with srv:
        assert probe.detect_mode(_settings(live, _free_port())) is AccountMode.LIVE


def test_solo_responde_el_puerto_simulado_aunque_este_configurado_live():
    srv, paper = _listener()
    with srv:
        assert probe.detect_mode(_settings(_free_port(), paper, AccountMode.LIVE)) is AccountMode.PAPER


def test_ninguno_o_los_dos_respetan_el_modo_configurado():
    assert probe.detect_mode(_settings(_free_port(), _free_port(), AccountMode.LIVE)) is AccountMode.LIVE
    a, pa = _listener()
    b, pb = _listener()
    with a, b:
        assert probe.detect_mode(_settings(pa, pb, AccountMode.PAPER)) is AccountMode.PAPER
        assert probe.detect_mode(_settings(pa, pb, AccountMode.LIVE)) is AccountMode.LIVE
