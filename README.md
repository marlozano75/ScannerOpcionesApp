# ScannerOpcionesApp

Scanner de **puts vendidas** sobre una watchlist y panel de riesgo (Cushion, Look Ahead, Severidad IBKR,
Post-Expiration, VIX) y de diversificación sectorial para una cuenta IBKR Reg T.
Solo lectura: **nunca envía órdenes** (el margen se calcula con órdenes *what-if*).

Especificación completa: [`ESPECIFICACION_TECNICA.md`](ESPECIFICACION_TECNICA.md).

## Puesta en marcha

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
copy config\config.example.yaml config\config.yaml     # ajusta puertos, umbrales, etc.
.venv\Scripts\python -m scanner_opciones.main           # http://127.0.0.1:8000
```

Requisitos: TWS o IB Gateway abierto con la API activada (Configuración > API), puerto 7497 (simulada)
o 7496 (real). La app arranca aunque TWS no esté disponible y lo indica en pantalla.

## Uso

- **Watchlist:** pega tickers o carga `.txt`/`.csv`/`.xlsx`. Al añadir tickers nuevos se les aplica la
  actualización diaria en el acto. La actualización diaria también corre al arrancar si no se hizo hoy, en segundo plano: la pantalla muestra primero los datos ya guardados.
- El precio de cada subyacente se refresca en cada ciclo (columna **Desc.** = descuento del strike sobre ese precio). Cada contrato muestra también el **Bid size**.
- **IV Rank:** rango = mayor máximo / menor mínimo diarios de la IV en 365 días, con la IV en directo como valor actual. **IV Percentile:** % de días (cierres) con IV menor. Difiere de TWS en unos pocos puntos y bastante en valores con picos en el historial de IBKR.
- **Precio de referencia:** con spreads anchos el mid es optimista; el selector del scanner permite calcular el yield con el bid, el mid o bid + X % del spread. La tabla muestra la prima usada y el yield al bid.
- **Ordenar:** en la watchlist y en los resultados del scanner, pulsa el título de una columna para ordenar (mayor a menor y, al pulsar otra vez, al revés).
- Al **quitar** un ticker se borran sus contratos y cotizaciones. **Forzar actualización diaria** corre en segundo plano (verás el progreso arriba) y espera su turno si hay otra tarea en curso. Solo valida con IBKR las combinaciones strike/vencimiento nuevas; **Revalidar contratos** vuelve a comprobar también las que IBKR no listaba (úsalo si echas en falta strikes).
- **Scanner:** operación Regular (descuento mín. del strike 20 %, DTE 25–35) o Táctica (descuento mín. 10 %, solo DTE máx. 15); todo editable,
  yield bruto ≥ 1 % (prima = precio de venta de referencia ÷ strike: Bid, Mid o Bid + X % del spread; por defecto Bid + 25 %). El descuento del strike (mín./máx.) y el yield bruto mínimo se editan en el propio formulario. Filtros opcionales (OI, spread, IV Rank, IV Percentile): solo se aplican si marcas su casilla.
- **Simulador:** marca contratos en el scanner y pulsa *Simular seleccionados*.
- **Panel:** cushion de IBKR con semáforo (> 40 % verde, 30–40 % ámbar, ≤ 30 % rojo), Look Ahead, Post-Expiration y Severidad IBKR (0 verde, 1 ámbar, 2 naranja, 3 rojo), VIX y diversificación.
- Exposición: **Gross Position Value**, **Nominal Assignment Exposure** (short puts − long puts) y **Leverage Assignment** (NAE / NLV).
- Selector **cuenta real / simulada** en la cabecera.

## Rango guardado y cotizaciones

La actualización diaria guarda los contratos con strike de −10 % a −40 % y DTE hasta 45 días (`scanner.candidates`; si tu `config/config.yaml` es anterior, ajusta esos valores). Cotizarlos todos cada 5 minutos sería demasiado (y toparía con los límites de IBKR), así que el refresco automático solo cotiza los que encajan con los valores iniciales de Regular o Táctica. Si cambias el rango en el formulario, pulsa **Actualizar cotizaciones de este rango y escanear**. Con el **mercado cerrado** (horario en `market`, por defecto 9:30–16:00 de Nueva York) el refresco automático hace una captura completa y después solo actualiza cartera y VIX hasta la apertura; **Refrescar ahora** siempre cotiza. Los festivos se anotan en `market.holidays`. La pestaña **Contratos** (o el enlace «Ver todos los contratos guardados» del scanner, que se abre en otra pestaña) muestra todos los contratos guardados con las mismas columnas, incluidos los que aún no tienen cotización.

## Configuración

Todo en `config/config.yaml` (ver `config/config.example.yaml`): cada X minutos de refresco, rangos de
DTE y de strike, yield mínimo, filtros, umbrales del semáforo, semanas de diversificación, etc.

## Tests

```powershell
.venv\Scripts\python -m pytest                          # unitarios + integración (sin TWS)
.venv\Scripts\python -m pytest tests/manual -m manual -s   # requiere TWS abierto
```

## Pendiente de verificar contra TWS real

- **HighestSeverity:** la API no lo devolvió en la cuenta simulada (2026-09-29); el panel lo muestra como "Sin datos" hasta que IBKR lo envíe (¿solo cuando no es 0, o solo en cuenta real?).
- Que las órdenes *what-if* funcionan con tu configuración de API (solo lectura o no).
- Cotizaciones de opciones, OI, dividendos (tick 456) y futuros VIX con tus suscripciones.

## Estructura

`src/scanner_opciones/`: `config`, `domain`, `metrics`, `watchlist`, `storage`, `broker` (puerto +
`IBKRGateway` + `FakeGateway`), `scanner`, `portfolio`, `jobs`, `app` (servicio), `ui` (FastAPI + Jinja2).
