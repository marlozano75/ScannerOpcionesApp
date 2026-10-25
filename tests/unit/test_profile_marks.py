from scanner_opciones.scanner.profile_marks import SMALL, _sev


def test_cumple_no_marca():
    assert _sev(20.0, 15.0, minimum=False) is not None          # 20 supera el máximo 15
    assert _sev(10.0, 15.0, minimum=False) is None
    assert _sev(None, 15.0, minimum=False) is None and _sev(10.0, None, minimum=False) is None


def test_pequeno_es_warn_y_grande_es_bad():
    assert _sev(16.0, 15.0, minimum=False) == "warn"            # 1 sobre 15 = 7 %
    assert _sev(30.0, 15.0, minimum=False) == "bad"
    assert _sev(27.0, 30.0, minimum=True) == "warn"
    assert _sev(10.0, 30.0, minimum=True) == "bad"
    assert SMALL == 0.25
