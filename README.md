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

- **Watchlist:** pestaña de solo lectura con la lista actual; se cambia desde **Universo**. Al añadir tickers nuevos se les aplica la
  actualización diaria en el acto. La actualización diaria también corre al arrancar si no se hizo hoy, en segundo plano: la pantalla muestra primero los datos ya guardados.
- El precio de cada subyacente se refresca en cada ciclo (columna **Desc.** = descuento del strike sobre ese precio). Cada contrato muestra también el **Bid size**.
- **IV Rank / IV Percentile:** los calcula tastytrade (API de solo lectura con OAuth: `tastytrade.client_secret` y `tastytrade.refresh_token` en `config.yaml`, obligatorios). Se piden todos los tickers en una sola petición, sin descargar historial de IV de IBKR. Si tastytrade no devuelve un ticker o falla, se conserva el último valor guardado.
- **Origen de los datos de mercado:** con `market_data.source: tastytrade` (por defecto) la cadena, las cotizaciones de opciones, los precios y los ex-dividendos vienen de tastytrade por DXLink (mucho más rápido: la cadena lista solo contratos que existen, sin validarlos uno a uno en TWS). El VIX y sus futuros también vienen de tastytrade. La cuenta, las posiciones, el margen what-if y el sector siguen viniendo de IBKR; la cuenta y el VIX se refrescan cada minuto, aparte del ciclo de mercado. Si tastytrade falla, cada consulta cae a IBKR. `market_data.source: ibkr` vuelve al comportamiento anterior (también disponible en la etiqueta git `version-ibkr-datos`). El lote de cotizaciones es `market_data.quote_batch_size` (2500).
- **Calidad de la empresa:** el scanner puede exigir beneficios en los últimos 12 meses, 2-4 de los últimos 4 trimestres con beneficios, una liquidez mínima de las opciones y evitar los vencimientos posteriores a la próxima fecha de resultados (se aplica contrato a contrato). Los datos vienen de tastytrade y se actualizan al arrancar y tras cada actualización diaria; el historial trimestral, cada 7 días. Las columnas EPS 12 m, Trim. +, Cap., Liq. opc. y Resultados se ven en la tabla.
- **Filtrar antes de la watchlist:** la pestaña **Universo** tiene un panel de filtros de calidad (beneficios, trimestres con beneficios, pasivo/patrimonio y flujo de caja libre) y columnas con esos indicadores, incluida la liquidez de las opciones (que solo se filtra en el Scanner). Solo quedan marcadas las acciones que cumplen, y «Añadir» o «Sustituir» pasan a la watchlist únicamente esas. Los datos de calidad se descargan para todo el Universo, no solo para la watchlist.
- **Solvencia con grados de exigencia:** en el Universo, además del pasivo/patrimonio y el flujo de caja, puedes filtrar por deuda/patrimonio, cobertura de intereses, efectivo frente a deuda a corto plazo y flujo operativo sobre deuda, eligiendo un grado **Flexible, Estándar o Estricto** (un selector pone el mismo grado a los cuatro; cada opción muestra su umbral). También hay tres indicadores opcionales: CapEx/flujo operativo, FCF/activos y recompra neta de acciones. Los umbrales están en `scanner.quality.thresholds` y se pueden cambiar. Los sectores financiero, energía, utilities, materiales e inmobiliario no se miden en esto (se ven como «n/a» y pasan); la lista es `scanner.quality.exempt_sectors`.
- **Apalancamiento y caja:** el scanner puede exigir un pasivo/patrimonio máximo y un flujo de caja libre positivo en los últimos 12 meses (las financieras quedan exentas). Los datos salen de la SEC (EDGAR, gratuito): pon tu correo en `edgar.contact` de `config.yaml` (la SEC lo exige en las peticiones; sin él no se consulta). Se actualizan cada 14 días; los emisores extranjeros (ADR) suelen no tener datos.
- **Precio de referencia:** con spreads anchos el mid es optimista; el selector del scanner permite calcular el yield con el bid, el mid o bid + X % del spread. La tabla muestra la prima usada y el yield al bid.
- **Ordenar:** en la watchlist y en los resultados del scanner, pulsa el título de una columna para ordenar (mayor a menor y, al pulsar otra vez, al revés). Los resultados del scanner salen ordenados por **Yield anual** (la columna destacada) mientras no elijas otra; las columnas van agrupadas: ticker, sector y precio; strike, descuento y DTE (el vencimiento sale al pasar el ratón por el DTE); yield anual y yield bid anual; delta con IV, IV Rank e IV Percentil; spread, bid size, OI y liquidez; calidad de la empresa; y pesos de cartera.
- Al **quitar** un ticker (al sustituir la watchlist desde Universo) se borran sus contratos y cotizaciones. Pide confirmación y no hace nada si la selección está vacía.
- **Universo:** la pestaña carga el .xlsx que tú descargas de RankedStocks y, de HelloStocks, su .xlsx o **la página de estrategias guardada desde el navegador (.html, con las listas desplegadas; `universe/hellostocks_html.py`: cada estrategia es una fuente con el nombre de la antigua pestaña, se omite la columna de miembros y al cargar se avisa de las listas que no se guardaron)** (eliges los ficheros; la app no se conecta a ninguna web ni lee carpetas). Cada **fuente** tiene sus propias columnas: RankedStocks es una fuente («RankedStocks») y cada pestaña del libro de HelloStocks es otra (su nombre es la fuente). Se elige la fuente con los botones de arriba; la vista «Todas» une los tickers (una fila por ticker con las fuentes en las que aparece). Las fuentes están numeradas y cada fila indica los números de las fuentes a las que pertenece; al cargar un fichero se descartan los tickers sin opciones. Puedes escribir tickers (separados por comas o espacios) en «Añadir tickers», poniendo un **nombre de fuente** (nuevo o existente): forman una fuente propia que se conserva al reiniciar. Al elegir una fuente se explica con qué criterios selecciona sus acciones (en HelloStocks, con los umbrales exactos si guardas la página con «Strategy Criteria» abierto) y se pueden **quitar sus tickers** (los marcados o toda la fuente; un fichero nuevo de esa fuente la restaura). Ya no hay botón para quitar un fichero. Los **filtros de calidad de la empresa están en el Scanner** (solvencia con grados, «deuda baja o manejable», combinaciones «Caja (4 filtros)» y «Deuda sana»); el Universo conserva sus columnas como información. Cada opción indica cuántos tickers de la watchlist la pasan y el panel «¿Cuánto descarta cada filtro?» muestra lo restrictiva que es la selección. Ticker, empresa y sector van siempre en las primeras columnas; en las fuentes propias se completan con las otras listas, el nombre de tastytrade y el sector de IBKR (si TWS está conectado). Se puede **añadir** la selección a la watchlist o **sustituirla**. Una descarga nueva de la misma fuente sustituye a la anterior y los ficheros se conservan al reiniciar (`data/universe/`). Los `.xlsx` de `watchlists/` están en `.gitignore`.
- **Scanner:** un único filtro editable (descuento del strike 10 %–30 %, DTE 1–35);
  yield anual ≥ 12 % (≈ 1 % bruto a 30 días; yield anual = prima ÷ strike × 365 ÷ DTE; prima = precio de venta de referencia ÷ strike: Bid, Mid o Bid + X % del spread; por defecto Bid + 25 %). El descuento del strike (mín./máx.) y el yield anual mínimo se editan en el propio formulario. Filtros opcionales (Bid mínimo, OI, Bid size, spread, IV Rank, IV Percentile): solo se aplican si marcas su casilla; **Bid mínimo (0,01 $) empieza marcado** porque un bid de 0 no se puede vender y, con el precio mid, inflaba el yield. El «Yield bid» de un bid 0 se muestra como «—».
- **Simulador:** marca contratos en el scanner y pulsa *Simular seleccionados*. Compara antes y después el cushion, el apalancamiento por asignación y la distribución por sector (total y por semana de vencimiento).
- **Panel:** cushion de IBKR con semáforo (> 40 % verde, 30–40 % ámbar, ≤ 30 % rojo), Look Ahead, Post-Expiration y Severidad IBKR (0 verde, 1 ámbar, 2 naranja, 3 rojo), VIX y diversificación.
- Exposición: **Gross Position Value**, **Nominal Assignment Exposure** (short puts − long puts) y **Leverage Assignment** (NAE / NLV).
- Selector **cuenta real / simulada** en la cabecera.

## Rango guardado y cotizaciones

La actualización diaria guarda los contratos con strike de −10 % a −30 % y DTE hasta 35 días (`scanner.candidates`, igual que `scanner.initial`; si tu `config/config.yaml` es anterior, ajusta esos valores). El refresco automático cotiza todos los guardados, así que el formulario del scanner nunca pide cotizaciones nuevas (ya no hay botón «Actualizar cotizaciones»). Se guarda además un margen (`scanner.catalog_margin_pct`, 5 puntos por arriba y por abajo: −5 %…−35 %) y cada refresco recalcula la ventana con el precio nuevo del subyacente: pide a IBKR solo los contratos que faltan y retira los que salen del margen, de modo que el rango cotizado sigue al precio. Ampliar el rango implica ampliar `scanner.candidates` (y cotizar más contratos, con los límites de IBKR). Con el **mercado cerrado** (horario en `market`, por defecto 9:30–16:00 de Nueva York) el refresco automático hace una captura completa (solo si aún no tienes guardadas las cotizaciones del cierre; se recuerda aunque reinicies la app) y después solo actualiza cartera y VIX hasta la apertura; **Refrescar ahora** siempre cotiza, y tras la actualización diaria se cotizan solos los contratos que aún no tenían cotización. Al cargar muchos tickers nuevos la actualización diaria espera el límite de peticiones históricas de IBKR (unos 10 minutos por cada 50 tickers); la cabecera lo indica. Los festivos se anotan en `market.holidays`. La columna **Ticker** queda fija al desplazar las tablas del scanner y de Contratos a la derecha. La pestaña **Contratos** (o el enlace «Ver todos los contratos guardados» del scanner, que se abre en otra pestaña) muestra todos los contratos guardados con las mismas columnas, incluidos los que aún no tienen cotización.

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
