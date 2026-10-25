from scanner_opciones.scanner.profiles import profile_diff


def test_igual_no_marca_nada():
    assert profile_diff({"discount": "10", "use_ivr": "on"}, {"discount": "10.0", "use_ivr": "on", "ma50": "any"}) == {}


def test_variacion_pequena_es_warn_y_grande_es_bad():
    d = profile_diff({"dte_min": "30", "discount": "10"}, {"dte_min": "35", "discount": "20"})
    assert d == {"dte_min": "warn", "discount": "bad"}


def test_filtro_marcado_o_desmarcado_es_bad():
    assert profile_diff({}, {"use_rsi": "on"}) == {"use_rsi": "bad"}
    assert profile_diff({"use_rsi": "on"}, {}) == {"use_rsi": "bad"}


def test_ignora_claves_de_navegacion():
    assert profile_diff({"profile": "A"}, {"profile": "B", "o_discount": "5", "mode": "put"}) == {}
