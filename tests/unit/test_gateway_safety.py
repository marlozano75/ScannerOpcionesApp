import inspect
import re

from scanner_opciones.broker import ibkr_gateway


def test_gateway_module_imports_and_never_places_orders():
    src = inspect.getsource(ibkr_gateway)
    code = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("#", '"""')))
    assert not re.search(r"\.placeOrder\(", code)
    assert "whatIfOrderAsync" in code
