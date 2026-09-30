# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Scanner de **puts vendidas** sobre una watchlist y panel de riesgo/diversificación para una cuenta IBKR Reg T (real o simulada). **Solo lectura: nunca envía órdenes** (el margen se calcula con órdenes *what-if*). El proyecto, la documentación y la interfaz están en español; mantén ese idioma en mensajes, comentarios y docs.

## Comandos

Entorno: Windows, Python ≥ 3.11 con un venv en `.venv`. En el shell Bash usa barras normales (`.venv/Scripts/python`); en PowerShell, `.venv\Scripts\python`.

```bash
.venv/Scripts/python -m pip install -e ".[dev]"          # instalar
cp config/config.example.yaml config/config.yaml         # la app lee config.yaml (gitignored), NO el example
.venv/Scripts/python -m scanner_opciones.main            # arranca en http://127.0.0.1:8000 (opciones: --config, --host, --port)

.venv/Scripts/python -m pytest -q                        # todo (sin TWS); los tests `manual` se excluyen por defecto
.venv/Scripts/python -m pytest tests/unit/test_metrics.py::TestReferencePrice::test_modes   # un solo test
.venv/Scripts/python -m pytest tests/manual -m manual -s # smoke tests contra un TWS real abierto (clientId = config + 100)
```

- `asyncio_mode = "auto"`: los tests `async def` no necesitan decorador.
- `tests/unit/test_sortable_columns.py` ejecuta `tests/js/sortable.test.js` con **Node** (se omite si no hay `node`).
- No hay linter configurado.
- TWS/IB Gateway debe tener la API activada: 7497 (simulada) / 7496 (real). Si TWS no está, la app arranca igual y muestra el error de conexión en pantalla.

## Arquitectura

Hexagonal ligera. El dominio y los jobs solo conocen el puerto `BrokerGateway` (`broker/base.py`, un `Protocol`); hay dos implementaciones: `IBKRGateway` (`ib_async`) y `FakeGateway` (memoria, usada por casi todos los tests). **Nada fuera de `broker/ibkr_*` debe importar `ib_async`.** Un método nuevo del broker se añade en `base.py`, `fake_gateway.py` e `ibkr_gateway.py`.

**Flujo de datos (lo que no se ve leyendo un solo archivo):**
1. **Actualización diaria** (`jobs/daily_update.py`, por ticker): sector, precio, cadena, ex-dividendo, historial de IV incremental, y **guarda** los contratos dentro del rango `scanner.candidates` (strike −10 %…−45 %, DTE 1…60), validándolos con `qualify_contracts` (los strikes de la cadena no existen para todos los vencimientos; aquí se guarda su `con_id`).
2. **Refresco periódico** (`jobs/refresh.py`, cada `refresh.interval_minutes`): actualiza precio e IV en directo de los subyacentes (recalcula IV Rank/Percentile), cotiza por lotes **solo** los contratos que encajan con los valores iniciales de Regular/Táctica (no todo el rango guardado: sería inviable por límites de IBKR) y guarda un *snapshot* por contrato. El what-if de margen se pide solo para los que pasan el escaneo.
3. **Escaneo** (`scanner/engine.py`) se calcula **al pedir la página**, sobre snapshots guardados, con un `ScanCriteria` que el formulario puede sobrescribir. El yield se calcula aquí a partir de `bid`/`ask` según el precio de referencia elegido (`metrics/yields.py`); el `yield_pct` guardado en el snapshot es el del mid y ya no se usa para filtrar.
4. `ScanCriteria.strike_below_pct_min` es el descuento **mínimo**; el máximo es el límite de lo guardado.

**`app/service.py` (`AppService`)** orquesta todo y guarda el estado en memoria (`AppState`: cuenta, posiciones, riesgo, VIX, actividad, errores). Un único `asyncio.Lock` impide ejecuciones solapadas: los jobs automáticos se **omiten** si está ocupado; las acciones manuales (`run_daily(wait=True)` vía `launch`) **esperan su turno**. `cleanup_orphans()` borra contratos/fichas de tickers que ya no están en la watchlist (al arrancar y antes de cada refresco). Cambiar real ↔ simulada (`switch_gateway`) descarta el estado de cuenta.

**Config** (`config/settings.py`): pydantic `frozen` + `extra="forbid"`; una clave desconocida o renombrada hace fallar el arranque con `ConfigError`. Cualquier valor de negocio va aquí, no en el código.

**Persistencia** (`storage/`): SQLite; las migraciones son una lista **solo de añadir** en `db.py::MIGRATIONS` (versión en `PRAGMA user_version`). Borrar un contrato borra sus snapshots en cascada. Las barras antiguas de IV sin máx/mín fuerzan una descarga completa única.

**UI** (`ui/`): FastAPI + Jinja2 sin lógica de negocio. El formulario del scanner se interpreta en `web.py::parse_scan` (GET y POST comparten parser). La ordenación de columnas es JS cliente (`templates/sortable.js`, incluido inline en `base.html`). Al añadir `.js`/`.html` nuevos, revisa `package-data` en `pyproject.toml`.

## Particularidades de IBKR (verificadas contra TWS, no las des por supuestas)

- La API **no expone** IV Rank, IV Percentile ni IV High/Low de 52 semanas. Se calculan: rank con el mayor máximo/menor mínimo **diarios** de las barras `OPTION_IMPLIED_VOLATILITY` de 365 días; percentil con los cierres. No coinciden exactamente con TWS (difiere sobre todo con picos en el historial, p. ej. FSLY).
- Overnight Excess **no existe** en la API; se muestra `HighestSeverity` (puede no llegar en cuenta simulada → «Sin datos»). `PostExpirationExcess = 0` se trata como «Sin datos», no como riesgo. El Cushion actual es el tag `Cushion` de IBKR, no se recalcula.
- `reqTickersAsync` **se cuelga** sin suscripción en tiempo real: usa `reqMktData` con espera acotada y timeouts (`_historical`, `_live_price`). VIX y futuros VIX se obtienen solo con barras históricas (`useRTH=False` para CFE).
- Errores benignos que verás en consola: 200 (strike inexistente), 10349 (TIF, ya evitado con `tif="DAY"`), 162/10197 (otra sesión del mismo usuario compite por datos: cerrar otras sesiones o usar `ibkr.market_data_type: 3`).
- `tests/unit/test_gateway_safety.py` verifica que el gateway nunca llama a `placeOrder`; no lo rompas.
- Límite conocido: la cuenta es EUR pero strikes y posiciones son USD; los pesos y el apalancamiento **no convierten moneda**.

## Documentación de referencia

`ESPECIFICACION_TECNICA.md` recoge los requisitos numerados (RF-xx) y las decisiones del usuario (Q-xx); al cambiar un comportamiento, actualízala junto con `README.md`. `especificaciones.md` es el requisito original.

Git: el repositorio de esta app está anidado dentro de `C:\Users\marlo\ProyectosVSC` (que es otro repositorio) y no tiene remoto. Los commits llevan el trailer `Co-Authored-By: Claude`.
