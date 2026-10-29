import socket

from scanner_opciones.broker import probe
from scanner_opciones.config.settings import IbkrSettings, Ports
from scanner_opciones.domain.enums import AccountMode, Platform


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


def _four(tws_live=None, tws_paper=None, gw_live=None, gw_paper=None, **kw):
    free = _free_port
    ports = Ports(live=tws_live or free(), paper=tws_paper or free(), gateway_live=gw_live or free(), gateway_paper=gw_paper or free())
    return IbkrSettings(ports=ports, **kw)


def test_detecta_el_gateway_simulado():
    srv, gw = _listener()
    with srv:
        s = _four(gw_paper=gw)
        assert probe.detect_endpoint(s) == (Platform.GATEWAY, AccountMode.PAPER)
        assert [e.key for e in probe.scan_endpoints(s) if e.active] == ["gateway:paper"]


def test_con_tws_y_gateway_abiertos_manda_el_configurado_y_si_no_el_primero():
    a, tws = _listener()
    b, gw = _listener()
    with a, b:
        both = dict(tws_paper=tws, gw_paper=gw)
        assert probe.detect_endpoint(_four(platform=Platform.GATEWAY, **both)) == (Platform.GATEWAY, AccountMode.PAPER)
        assert probe.detect_endpoint(_four(platform=Platform.TWS, **both)) == (Platform.TWS, AccountMode.PAPER)
        # el configurado (TWS real) no responde: se elige el primero que sí, TWS antes que Gateway
        assert probe.detect_endpoint(_four(platform=Platform.TWS, mode=AccountMode.LIVE, **both)) == (Platform.TWS, AccountMode.PAPER)


def test_sin_ningun_destino_activo_se_respeta_la_configuracion():
    s = _four(platform=Platform.GATEWAY, mode=AccountMode.LIVE)
    assert probe.detect_endpoint(s) == (Platform.GATEWAY, AccountMode.LIVE)
    assert s.port == s.ports.gateway_live


def test_ninguno_o_los_dos_respetan_el_modo_configurado():
    assert probe.detect_mode(_settings(_free_port(), _free_port(), AccountMode.LIVE)) is AccountMode.LIVE
    a, pa = _listener()
    b, pb = _listener()
    with a, b:
        assert probe.detect_mode(_settings(pa, pb, AccountMode.PAPER)) is AccountMode.PAPER
        assert probe.detect_mode(_settings(pa, pb, AccountMode.LIVE)) is AccountMode.LIVE
