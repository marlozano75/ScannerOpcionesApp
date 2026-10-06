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
- **IV Rank / IV Percentile:** los calcula tastytrade (API de solo lectura con OAuth: `tastytrade.client_secret` y `tastytrade.refresh_token` en `config.yaml`, obligatorios). Se piden todos los tickers en una sola petición, sin descargar historial de IV de IBKR. Si tastytrade no devuelve un ticker o falla, se conserva el último valor guardado.
- **Precio de referencia:** con spreads anchos el mid es optimista; el selector del scanner permite calcular el yield con el bid, el mid o bid + X % del spread. La tabla muestra la prima usada y el yield al bid.
- **Ordenar:** en la watchlist y en los resultados del scanner, pulsa el título de una columna para ordenar (mayor a menor y, al pulsar otra vez, al revés).
- Al **quitar** un ticker se borran sus contratos y cotizaciones. **Forzar actualización diaria** corre en segundo plano (verás el progreso arriba) y espera su turno si hay otra tarea en curso. Solo valida con IBKR las combinaciones strike/vencimiento nuevas; **Revalidar contratos** vuelve a comprobar también las que IBKR no listaba (úsalo si echas en falta strikes).
- **Sustituir la watchlist:** además de añadir, puedes reemplazarla entera con el texto pegado o con un archivo (botones «Sustituir watchlist»): se quitan los tickers que no estén en la lista nueva (con sus contratos), se conservan los que siguen y se añaden los nuevos. Pide confirmación y no hace nada si la lista no trae ningún ticker válido.
- **RankedStocks:** la pestaña carga el .xlsx que tú descargas de rankedstocks.com (eliges el fichero; la app no se conecta a su web ni lee carpetas), muestra sus columnas, filtra por cada una (texto, lista de valores o rango numérico) y permite **añadir** la selección a la watchlist o **sustituirla**. El fichero queda en memoria hasta que reinicies la app. Los `.xlsx` de `watchlists/RankedStocks_*.xlsx` están en `.gitignore`.
- **Scanner:** un único filtro editable (descuento del strike 10 %–30 %, DTE 1–35), con una columna **Operación** que marca **Regular** (DTE 25–35) o **Táctica** (el resto);
  yield anual ≥ 12 % (≈ 1 % bruto a 30 días; yield anual = prima ÷ strike × 365 ÷ DTE; prima = precio de venta de referencia ÷ strike: Bid, Mid o Bid + X % del spread; por defecto Bid + 25 %). El descuento del strike (mín./máx.) y el yield anual mínimo se editan en el propio formulario. Filtros opcionales (OI, Bid size, spread, IV Rank, IV Percentile): solo se aplican si marcas su casilla.
- **Simulador:** marca contratos en el scanner y pulsa *Simular seleccionados*. Compara antes y después el cushion, el apalancamiento por asignación y la distribución por sector (total y por semana de vencimiento).
- **Panel:** cushion de IBKR con semáforo (> 40 % verde, 30–40 % ámbar, ≤ 30 % rojo), Look Ahead, Post-Expiration y Severidad IBKR (0 verde, 1 ámbar, 2 naranja, 3 rojo), VIX y diversificación.
- Exposición: **Gross Position Value**, **Nominal Assignment Exposure** (short puts − long puts) y **Leverage Assignment** (NAE / NLV).
- Selector **cuenta real / simulada** en la cabecera.

## Rango guardado y cotizaciones

La actualización diaria guarda los contratos con strike de −5 % a −35 % y DTE hasta 45 días (`scanner.candidates`; si tu `config/config.yaml` es anterior, ajusta esos valores). Cotizarlos todos cada 5 minutos sería demasiado (y toparía con los límites de IBKR), así que el refresco automático solo cotiza los que encajan con los valores iniciales del filtro (`scanner.initial`). Si cambias el rango en el formulario, pulsa **Actualizar cotizaciones de este rango y escanear**. Con el **mercado cerrado** (horario en `market`, por defecto 9:30–16:00 de Nueva York) el refresco automático hace una captura completa (solo si aún no tienes guardadas las cotizaciones del cierre; se recuerda aunque reinicies la app) y después solo actualiza cartera y VIX hasta la apertura; **Refrescar ahora** siempre cotiza, y tras la actualización diaria se cotizan solos los contratos que aún no tenían cotización. Al cargar muchos tickers nuevos la actualización diaria espera el límite de peticiones históricas de IBKR (unos 10 minutos por cada 50 tickers); la cabecera lo indica. Los festivos se anotan en `market.holidays`. La columna **Ticker** queda fija al desplazar las tablas del scanner y de Contratos a la derecha. La pestaña **Contratos** (o el enlace «Ver todos los contratos guardados» del scanner, que se abre en otra pestaña) muestra todos los contratos guardados con las mismas columnas, incluidos los que aún no tienen cotización.

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
