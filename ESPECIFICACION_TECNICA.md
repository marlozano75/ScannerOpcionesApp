# Especificación técnica — ScannerOpcionesApp

> Documento destinado a un agente de IA implementador. Deriva **exclusivamente** de `especificaciones.md`.
> **Estado: LISTO PARA IMPLEMENTAR.** Todas las preguntas están resueltas o aceptadas con su valor provisional (marcado "ACEPTADO"); el usuario puede cambiarlos vía configuración.
> Convenciones:
> - **[REQ]** = requisito explícito en la especificación original.
> - **[PROPUESTA]** = decisión técnica propuesta por el analista (no viene del usuario; modificable).
> - **PENDIENTE (Q-xx)** = información que falta. El implementador **NO debe decidirla por su cuenta**: debe usar el valor por defecto indicado (si existe), dejarlo configurable y marcar el punto con `# TODO(Q-xx)`.

---

## 1. Resumen y alcance

Aplicación de escritorio/local que, conectada a TWS de Interactive Brokers (cuenta de margen Reg T, Irlanda; real o simulada), ofrece:

1. **Scanner de opciones** sobre una watchlist (un único filtro editable).
2. **Panel de riesgo** por apalancamiento (Cushion, semáforo, Look Ahead / Post-Expiration / Severidad IBKR, VIX).
3. **Panel de diversificación** sectorial (actual y próximas 5 semanas).
4. **Simulador** de cartera si se ejercen contratos seleccionados.

**Decisiones confirmadas (Q-01):** el scanner es **solo para venta de puts**; la aplicación es de solo lectura y **nunca envía órdenes**. Solo usa órdenes *what-if* para calcular margen.

---

## 2. Requisitos funcionales

| ID | Requisito | Origen |
|----|-----------|--------|
| RF-01 | Conectar a TWS vía API. Selector cuenta **real** / **simulada** (puertos y clientId configurables). | L3 |
| RF-02 | Introducir watchlist pegando tickers (texto libre). | L10 |
| RF-03 | Introducir watchlist cargando archivo Excel (.xlsx) o texto (.txt/.csv). | L10 |
| RF-04 | Actualización **diaria** por ticker: sector, categoría, expiraciones, DTE, strikes, contratos candidatos, días hasta ex-dividendo (si aplica); IV Rank e IV Percentile de tastytrade. | L12-20 |
| RF-05 | Los tickers añadidos **después** de la ejecución diaria se actualizan (bloque diario) en el momento de añadirse. | L12 |
| RF-06 | [SUSTITUIDO 2026-10-06 por RF-28/RF-40] Antes: historial de IV persistido y descargado de forma incremental. Ya no se descarga historial de IV. | L20 |
| RF-07 | Cada **X minutos** (configurable) refrescar por contrato: Bid, Ask, Delta, IV, Last, OI, timestamp de última actualización; calcular Spread %, Yield, Yield anualizado, IV Rank, IV Percentile, margen inicial si se ejecuta. | L21-34 |
| RF-08 | Scanner de puts vendidas con **un único filtro** (sin selector Regular/Táctica): descuento mínimo (inicial **10 %**) y máximo (inicial **30 %**) del strike respecto al precio, **yield anual** mínimo (inicial **12 %**; antes yield bruto 1 %, cambio del 2026-10-01), DTE mín. y máx. (inicial **1 y 35**). Todo editable en el formulario. | L6 + decisión del usuario 2026-10-01 |
| RF-09 | Scanner **Táctico**: descuento mínimo del strike (inicial **10 %**), yield bruto mínimo (inicial 1 %) y **solo DTE máx.** (inicial **15**; el mínimo es 1 y no se muestra). Todo editable. | L7 + decisión del usuario 2026-09-29 |
| RF-10 | Filtros adicionales: OI mínimo, Spread máximo, IV Rank, IV Percentile. | L8 |
| RF-11 | Al ejecutar el escaneo se muestran **solo** contratos que cumplen los criterios. | L36 |
| RF-12 | Cada contrato mostrado incluye: incremento del peso de su sector en la cartera si se toma; peso respecto al resto de contratos que expiran la misma semana. | L38 |
| RF-13 | Cada contrato incluye el % de cartera que supondría si es asignado al precio de strike. | L39 |
| RF-14 | Seleccionar varios contratos y simular diversificación y riesgo (cushion, márgenes, …). Mostrar cartera actual vs. futura si se ejercieran. | L42 | La simulación muestra el antes y el después de la distribución por sector (peso total con barras y peso por sector en cada semana de vencimiento).
| RF-15 | Panel de riesgo: Cushion de cartera + semáforo. | L44-48 |
| RF-16 | Mostrar Look Ahead y Post-Expiration con el semáforo de cushion, y la Severidad (`HighestSeverity` de IBKR) en lugar de Overnight (0 verde, 1 ámbar, 2 naranja, 3 rojo). | L50 + decisión del usuario |
| RF-17 | Mostrar VIX últimos 5 días, VIX actual y futuros VIX previstos a 2-3 semanas. | L52 |
| RF-18 | Panel de diversificación: en cada ejecución actualizar sectores y % de cartera por sector. | L56 |
| RF-19 | Diversificación sectorial para las próximas 5 semanas con contratos abiertos. | L57 |
| RF-20 | Panel de riesgo: mostrar **Gross Position Value** (tag `GrossPositionValue` de IBKR), **Nominal Assignment Exposure** = Short Put Exposure − Long Put Protection (nominal = strike × multiplicador × contratos) y **Leverage Assignment** = NAE / NLV. También antes/después en el simulador. | Petición del usuario 2026-09-29 |
| RF-21 | VIX y futuros VIX solo con barras históricas diarias (sin suscripción en tiempo real; futuros CFE con `useRTH=False`). Ningún paso de red puede colgarse: timeouts. | Petición del usuario 2026-09-29 |
| RF-23 | La actualización diaria guarda los contratos con strike de −10 % a −30 % y DTE hasta 35 días (configurable en `scanner.candidates`; antes −5 % a −35 % y 45 días, y antes −45 % y 60 días). Desde 2026-10-06 coincide con `scanner.initial`: cada ciclo automático cotiza todos los guardados y se eliminó el botón «Actualizar cotizaciones de este rango» y su ruta `/scanner/refresh`. | Petición del usuario 2026-09-29 |
| RF-24 | Al quitar un ticker de la watchlist se borran sus contratos (con sus cotizaciones) y su ficha. Al arrancar y antes de cada refresco se eliminan los datos de tickers que ya no están en la watchlist. | Petición del usuario 2026-09-29 |
| RF-25 | La actualización diaria forzada se ejecuta en segundo plano y espera su turno si hay otra tarea en curso (no se omite en silencio); la interfaz muestra la tarea en curso y su progreso. | Petición del usuario 2026-09-29 |
| RF-26 | El precio del subyacente se actualiza en cada ciclo de refresco (no solo en la actualización diaria); las distancias y el filtro de descuento usan ese precio. La columna se llama **Desc.** | Petición del usuario 2026-09-29 |
| RF-27 | Por contrato se trae y muestra también el **Bid size** (tamaño del bid). | Petición del usuario 2026-09-29 |
| RF-46 | **Catálogo dinámico con margen.** Lo guardado es el rango visible (`scanner.candidates`, −10 %…−30 %, DTE 1…35) ampliado con `scanner.catalog_margin_pct` (5) puntos por arriba y por abajo en los strikes (−5 %…−35 %). Cada refresco, tras actualizar el precio de los subyacentes, recalcula la ventana de cada ticker con el precio nuevo (`jobs/contract_sync.py::ContractSyncer`, compartido con la actualización diaria; la cadena se cachea en memoria por día): valida con IBKR y guarda solo los contratos que faltan, y retira los que quedan fuera del margen (con sus snapshots). La cotización sigue limitada a −10 %…−30 % **respecto al precio actual**, por lo que el foco se desplaza con el precio. Un fallo al sincronizar un ticker no interrumpe el refresco. | Petición del usuario 2026-10-06 |
| RF-29 | **Catálogo de contratos incremental.** La actualización diaria solo valida con IBKR (`qualify_contracts`) las combinaciones strike/vencimiento que no están guardadas ni se sabe que no existen; las inexistentes se recuerdan en `contract_misses` y no se reintentan (botón **Revalidar contratos** las olvida). Los contratos que siguen en la ventana conservan su snapshot; solo se borran los vencidos o fuera de la ventana. | Petición del usuario 2026-10-01 |
| RF-30 | **Arranque y actualización ligeros.** Al arrancar se refresca primero con lo ya guardado y la actualización diaria corre después en segundo plano (y, al acabar, un refresco cotiza lo nuevo). Precios y dividendos de todos los tickers se piden en lote, el sector guardado no se vuelve a pedir y los tickers se actualizan en paralelo (`daily_update.concurrency`). Las cotizaciones dejan de esperar `quote_wait_seconds` si ya llegaron todos los datos, y el margen (what-if) se reutiliza hasta `refresh.margin_max_age_minutes`. El log INFO recoge el tiempo por fase. | Petición del usuario 2026-10-01 |
| RF-31 | ~~Pestaña **Contratos**~~ **Eliminada** (2026-10-06, petición del usuario): el scanner ya cubre los contratos guardados y se retiraron la ruta `/contracts`, su plantilla y `stored_contracts`/`list_stored`. | Petición del usuario 2026-10-01; retirada 2026-10-06 |
| RF-32 | Un refresco que no recibe ni bid ni ask de un contrato (mercado cerrado o fallo puntual) **no pisa** su última cotización válida: conserva bid, ask, last y bid size con su hora original; igual con las griegas y el OI. Con el mercado cerrado tampoco pisa con un **bid de 0 y ask > 0** (cotización vacía de fuera de horario) si había un bid > 0. | Petición del usuario 2026-10-01 |
| RF-33 | **Refresco según el horario del mercado** (`market.*`, por defecto 9:30–16:00 hora de Nueva York, sin fines de semana ni festivos configurados). Con el mercado abierto el refresco automático es completo. Con el mercado cerrado hace **una captura completa** (datos congelados, para guardar el cierre) y después solo cartera y VIX hasta la apertura. La captura solo hace falta si el último refresco completo (marca persistente `meta.last_full_refresh_at`, sobrevive a reinicios) es anterior al último cierre de sesión: **si al abrir la app con el mercado cerrado las cotizaciones guardadas ya son del cierre, no se cotiza nada** ; tras la actualización diaria con el mercado cerrado se cotizan (datos congelados) solo los contratos guardados que nunca se habían cotizado, sin volver a pedir precio/IV de los subyacentes y sin tocar el marcador; «Refrescar ahora» y el arranque siguen siendo completos. La actualización diaria no depende del horario. Cabecera: aviso «Mercado cerrado». | Petición del usuario 2026-10-01 |
| RF-34 | El error 10197 de IBKR (sesión competidora de datos en directo) se resume en **un aviso por lote** con los tickers afectados (en vez de una línea ERROR por contrato); esos contratos conservan su última cotización (RF-32). El log INFO de `ib_async` (una línea por cada actualización de cartera) se oculta con `logging.ib_async_level: WARNING`. | Petición del usuario 2026-10-01 |
| RF-35 | La columna **Ticker** (y la casilla de selección del scanner) quedan fijas al desplazar la tabla a la derecha. Los contratos guardados pasan a strike −5 %…−40 %. | Petición del usuario 2026-10-01 |
| RF-38 | El scanner y Contratos muestran el **Precio** del subyacente y **Precio act.** (fecha y hora en que se obtuvo, `ticker_info.price_at`, migración v6): la actualiza el refresco y la actualización diaria; si no llega precio nuevo se conserva la fecha anterior. | Petición del usuario 2026-10-06 |
| RF-36 | **Sustituir la watchlist**: junto a «Añadir» y «Cargar archivo» hay botones «Sustituir watchlist» (con el texto pegado) y «Sustituir watchlist con el archivo». Piden confirmación; quitan, con sus contratos y cotizaciones, los tickers que no están en la lista nueva, conservan los que siguen (con sus datos) y añaden los nuevos con su actualización diaria. Una lista sin ningún ticker válido no cambia nada. | Petición del usuario 2026-10-01 |
| RF-37 | Pestaña **Universo** (`/universe`, sustituye a RankedStocks): el usuario **elige** uno o varios ficheros de RankedStocks (.xlsx) y HelloStocks (.xlsx o la página de estrategias guardada desde el navegador, .html; `universe/hellostocks_html.py` lee las tablas del DOM —no el JSON incrustado, que puede ser de otra carga—, usa como fuente el nombre de la antigua pestaña y avisa de las estrategias declaradas sin tabla o con menos filas que su «Holdings»; la app no se conecta a ninguna web). Cada hoja es una **fuente** con sus propias columnas: RankedStocks → «RankedStocks»; HelloStocks → el nombre de cada pestaña. Una columna «Fuente» va detrás del ticker; la pestaña de Buffett de HelloStocks viene sin cabecera y se le asignan las columnas deducidas (`universe/sources.py::HEADERLESS_COLUMNS`). Vista «Todas» (una fila por ticker: empresa, sector, fuentes, nº de fuentes) y una vista por fuente; filtros por columna (texto, lista, rango; admiten $, comas, K/M/B/T y «Billion/Million») y añadir/sustituir la watchlist con la selección. Los ficheros se guardan en `data/universe/` y se recuperan al arrancar; una descarga que aporte alguna fuente ya cargada sustituye al fichero anterior. No se puede quitar un fichero; sí **quitar tickers a nivel fuente** (los marcados o todos; en las de fichero la exclusión se recuerda hasta que se cargue un fichero nuevo de esa fuente). Los tickers escritos a mano van a **fuentes con nombre** (obligatorio al añadir). Los filtros de calidad de la empresa están en el Scanner (el Universo solo conserva sus columnas informativas), con ROIC, estabilidad de los beneficios (años con pérdidas de los últimos 10 años fiscales), «deuda baja o manejable», combinaciones preconfiguradas, recuento de tickers por opción y panel de impacto de cada filtro. Ticker, empresa y sector salen siempre en las primeras columnas (tras «En watchlist» y «Fuentes»); en las fuentes manuales se completan con las otras listas, el nombre de tastytrade y el sector de IBKR. Al elegir una fuente se muestra la explicación de sus criterios (`universe/descriptions.py`); en HelloStocks incluye los umbrales exactos si el .html se guardó con «Strategy Criteria» abierto. | Petición del usuario 2026-10-06 |
| RF-38 | Mientras la actualización diaria espera el límite de peticiones históricas de IBKR (`ibkr.historical_requests_per_10min`, 50 por 10 min; cada ticker nuevo descarga un año de histórico de IV), la cabecera lo dice («esperando el límite de peticiones históricas de IBKR, ≈ N min») y el log INFO lo registra una vez por espera. Cargar muchos tickers nuevos tarda por eso unos 10 minutos por cada 50. | Petición del usuario 2026-10-01 |
| RF-39 | **Log a fichero y contador de peticiones históricas.** Además de la consola, el log se escribe en `logging.file` (rotativo, ignorado por git). La pasarela cuenta las peticiones históricas por tipo (`precio`, `iv`, `vix`, `futuros_vix`); la actualización diaria registra cuántas ha gastado y el aviso del límite de IBKR (RF-38) incluye el total desde el arranque. | Petición del usuario 2026-10-05 |
| RF-28 | IV Rank e IV Percentile se toman de tastytrade en la actualización diaria y en cada refresco (una petición en lote); si un ticker no viene o el proveedor falla se conserva el valor guardado. Sin respaldo con IBKR. | Decisión del usuario 2026-10-06 |
| RF-40 | Se usa el rank principal de tastytrade (`implied_volatility_index_rank`) y `implied_volatility_percentile`, convertidos de fracción a %. Sustituye al antiguo RF-29 sobre máx/mín diarios de IBKR. | Decisión del usuario 2026-10-06 |
| RF-41 | Se elimina la columna **Operación** (Regular/Táctica) del scanner y de Contratos y la clave `scanner.operation` de la configuración (una `config.yaml` que la conserve falla al arrancar). No había nada en la base de datos: la etiqueta se calculaba al escanear. | Petición del usuario 2026-10-06 |
| RF-42 | El precio del subyacente de IBKR se **contrasta con el de tastytrade** (puerto `PriceProvider`, `get_market_data`, una petición para todos): si se aleja más de `tastytrade.price_max_deviation_pct` (5 %), se guarda el de tastytrade (`last`, o `mark` si no hay) y se deja un WARNING. Si tastytrade falla o no cubre el ticker, se conserva el de IBKR. Aplica a la actualización diaria y al refresco. Motivo: CBNK mostraba 51,9 con un cierre de 40,02. | Petición del usuario 2026-10-06 |
| RF-43 | **Histórico de cierres diarios** (base de los filtros técnicos de RF-44; configurable en `trend`): los cierres diarios se piden a tastytrade (DXLink, velas de 1 día, lotes de 50 suscripciones, sin pasar por el límite de históricos de IBKR) y **se guardan en la tabla `daily_bars`** (migración v8): la primera vez se descargan `trend.history_days` (1400, ver RF-44) días y después **solo los días que faltan** (desde el último día guardado, con 7 días de solape; si el último cierre guardado es de ayer no se pide nada). Si los cierres solapados no coinciden con los guardados (split o ajuste) se descarta el histórico del ticker y se vuelve a descargar entero; los cierres más antiguos que `history_days` se borran y los de tickers fuera de la watchlist también. (La columna **Tendencia** ↑/↓, el filtro «Solo tendencia alcista» precio > SMA50 > SMA200 y las medias SMA50/SMA200 que se guardaban en `ticker_info` se eliminaron el 2026-10-06, migración v12: los filtros de RF-44 calculan sus medias al escanear con estos cierres.) | Petición del usuario 2026-10-06 |
| RF-44 | **Filtros de tendencia y niveles** en el scanner (inspirados en las capturas `imagenes/Trendandlevels.png` y `imagenes/Min_Days_Since_Last_Touch.png`), calculados al escanear con los cierres diarios guardados (`daily_bars`, ahora ~2 años = 730 días; la migración v9 vacía el histórico para que la próxima actualización diaria lo descargue entero). (1) **Tendencia** Off / Alcista / Bajista con dos métodos, y una **ventana** de análisis de 1, 2, 3, 6, 9, 12, 18 o 24 meses (`scanner.technical.trend_windows_months`; por defecto 24 = todo el histórico). La tendencia se analiza **siempre con los cierres diarios de la ventana**, día a día (sin agrupar en semanas ni meses; las velas semanales/mensuales solo existen para las medias): «mínimo (máximo) sin romper» exige que el mínimo de la ventana (que ningún cierre posterior ha roto) tenga al menos 1, 2 o 3 semanas, 1, 2, 3, 4, 6 o 9 meses, o 1 año de antigüedad (`scanner.technical.trend_durations`) y que el precio haya avanzado `trend_min_progress_pct` (5 %) desde él; «máximos y mínimos crecientes (decrecientes)» compara los últimos máximos y mínimos locales de la ventana (`scanner.technical.trend_pivot_width` y `trend_swings_required`: días a cada lado de un pivote y nº de pivotes) y exige que **ningún cierre posterior al último mínimo (máximo), ni el precio actual, lo haya roto**; y un tercer método, **«solo mínimos crecientes (máximos decrecientes)»**, aplica la misma regla pero solo a los mínimos (alcista) o solo a los máximos (bajista) e ignora el otro lado. (2) **Zona de soporte probada**: nivel tocado (mínimos locales) 3+ veces dentro de una banda del 1,5 % en 2+ episodios separados ≥ 20 días durante el último año, sin cierres posteriores al primer toque que lo pierdan en más de 1,5 %; el strike de la put debe estar en la zona o por debajo (solo soporte: la app solo vende puts, no hay resistencias). (3) **Medias**: MA50, MA100, MA200, EMA9 y EMA20, cada una Cualquiera / Precio por encima / Precio por debajo, calculadas con **velas diarias, semanales o mensuales** (`ma_frame`; MA 50 = 50 días, 50 semanas o 50 meses, re-muestreando los cierres diarios) y **comparaciones entre ellas**: EMA9 ≥/≤ EMA20, EMA20 ≥/≤ MA50, MA50 ≥/≤ MA100 y MA100 ≥/≤ MA200, y **pendiente** de cada media (Cualquiera / Ascendente / Descendente): se compara su valor actual con el de hace `scanner.technical.ma_slope_candles` velas (5) de las elegidas; hacen falta periodo + N velas, si no el ticker se descarta. Con velas semanales o mensuales el formulario **no ofrece** las medias, comparaciones y pendientes que no caben en el histórico guardado (`criteria.unavailable_ma_fields`: con 1400 días, semanal sin MA200 y mensual sin MA50/100/200) y descarta su valor al escanear. Sin velas suficientes el ticker se descarta indicando cuántas había. El histórico guardado pasa a **1400 días** (~3,8 años, migración v11: lo máximo que entrega tastytrade, 944 velas diarias / 198 semanales / 47 mensuales), por lo que con velas semanales no se puede calcular la MA200 y con mensuales ninguna MA de 50 o más. (4) **Días mín. desde el último toque del strike**: se excluyen los strikes que un cierre (≤ strike) haya visitado dentro de ese plazo (`touch_min_days_options`: 10, 20, 30, 45, 60, 90, 120, 180, 252 y 365 días). Todos los controles escanean al cambiarse y viajan en la URL. Un ticker sin histórico queda descartado por cualquiera de estos filtros. (5) **Precio del subyacente** mín. y máx. (vacío = sin límite; p. ej. para excluir tickers caros), que no necesita histórico y se aplica con los filtros básicos (`ScanCriteria.min_price`/`max_price`). | Petición del usuario 2026-10-06 |
| RF-45 | El **histórico de cierres vive en la base de datos** (`daily_bars`) y persiste entre arranques: se completa **lo primero al arrancar** (antes de conectar con el broker y del primer refresco, que tarda minutos; ~10 s la primera vez con 139 tickers y <1 s después porque solo se piden los días que faltan) y en cada actualización diaria. Además, cada refresco (y la actualización diaria) guarda el **último precio del día** de cada ticker como **cierre provisional** de la sesión de hoy (`daily_bars.provisional = 1`, migración v10; solo si hoy hay sesión y ya ha abierto, `MarketCalendar.session_day`); cada refresco lo actualiza, nunca pisa un cierre oficial, no cuenta como «histórico al día» (se sigue pidiendo el oficial) ni se usa para detectar splits, y el cierre oficial lo sustituye al día siguiente. Los filtros técnicos usan, por tanto, el último valor disponible (cierre o precio de hoy). | Petición del usuario 2026-10-06 |
| RF-47 | **Gráfico del strike** en el Scanner: al pulsar un ticker de la tabla se abre una ventana con los cierres mensuales de los últimos `scanner.technical.chart_months` (24) frente al strike de ese contrato (`metrics/technical.py::strike_history`, `ui/charts.py`, endpoint `GET /chart/strike?ticker=&strike=`; SVG sin librerías). Muestra la línea del strike, el precio actual, los puntos en rojo (cierre ≤ strike), amarillo (a menos de `chart_near_pct` = 5 % por encima) o azul, un resumen «N de M meses por encima del strike · más cercano» y el **último toque**: el último cierre diario ≤ strike, con los **días transcurridos** (aro naranja en el gráfico y línea de texto; sin toques: «ningún cierre en o por debajo del strike en N días de histórico»). Cada fila de la tabla lleva además una **miniatura** (columna «Historial», imagen `GET /chart/mini.svg?ticker=&strike=` cargada al hacerse visible la fila, con el strike punteado, el último punto y el aro del último toque) que abre el gráfico grande. Usa los cierres de `daily_bars`; sin histórico, avisa. | Petición del usuario 2026-10-09 (referencia `imagenes/Strike History_SpreadVector.png`) |
| RF-30 | Las tablas de la watchlist y de resultados del scanner se ordenan pulsando el título de la columna: primer clic de mayor a menor (números) o de A a Z (texto), segundo clic al revés; los vacíos («—») quedan al final; el orden elegido se recuerda en la sesión. | Petición del usuario 2026-09-29 |
| RF-31 | El yield se calcula con un **precio de venta de referencia** seleccionable en el scanner: **Bid**, **Mid** o **Bid + X % del spread** (por defecto Bid + 25 %; `scanner.price_reference`). El filtro del yield mínimo y el yield anualizado usan esa referencia; se muestra también el «Yield bid anual» (lineal, ×365/DTE) para comparar (las columnas «Prima ref.», «Yield» y «Yield bid» se quitaron de la tabla el 2026-10-09, junto con Vence, Bid, Ask, Last, Margen ini., Hora precio, Ex-div, FCF y Actualizado). Sustituye a la prima = mid de Q-06. | Petición del usuario 2026-09-29 |
| RF-32 | Al validar contratos candidatos contra IBKR no deben llenarse los logs con un error por cada combinación inexistente: los avisos esperados («Error 200», «Unknown contract») se filtran durante la validación y se registra un resumen por ticker («N de M combinaciones existen»). Se descartó listar todas las opciones con `reqContractDetails` por lentitud (BAC 32 s frente a 5 s; MU > 100 s). | Petición del usuario 2026-09-30 |
| RF-22 | En el scanner, OI mín., Bid size mín. (2026-10-01), spread máx., IV Rank mín. e IV Percentile mín. son **opcionales**: se aplican solo si el usuario marca su casilla. El descuento del strike (mín./máx.) y el yield anual mínimo son editables en el formulario (valores iniciales de la configuración). | Petición del usuario 2026-09-29 |

## 3. Requisitos técnicos / no funcionales

| ID | Requisito |
|----|-----------|
| RT-01 | Fuente única de datos: API de TWS (o IB Gateway). No se usan otras fuentes salvo aprobación (ver Q-12). |
| RT-02 | Respetar límites de la API IBKR: líneas de market data simultáneas, pacing de históricos (≈60 peticiones/10 min), límites de what-if. Toda petición pasa por un limitador central. |
| RT-03 | Toda la configuración (puertos, X minutos, umbrales, semáforos, rangos DTE) en fichero externo; nada "hardcodeado". |
| RT-04 | Manejo de errores explícito: TWS desconectado, ticker inválido, sin suscripción de datos, sin datos (bid/ask = -1/NaN), timeouts. La app **degrada** (marca el dato como no disponible) sin caerse. |
| RT-05 | Persistencia local (SQLite [PROPUESTA]) para: watchlist, metadatos, historial IV, último snapshot de contratos. |
| RT-06 | Núcleo de negocio **independiente** de IBKR y de la UI (puertos/adaptadores) para poder testear con datos simulados. |
| RT-07 | Logging estructurado con niveles; los errores de TWS (códigos) se registran. |
| RT-08 | Cada valor mostrado indica su antigüedad (timestamp) [REQ para contratos; PROPUESTA para el resto]. |
| RT-09 | Ejecución en Windows 11 [entorno actual del usuario]. |

---

## 4. Módulos: entradas, proceso, salidas

### M1 — Configuración (`config`)
- **Entrada:** `config/config.yaml`, variables de entorno (`.env`).
- **Proceso:** carga y valida con esquema tipado; expone objeto inmutable.
- **Salida:** `Settings`. Error claro si falta/erróneo algún campo.

### M2 — Broker / Conexión IBKR (`broker`)
- **Entrada:** modo (real/simulada), host, puerto, clientId.
- **Proceso:** conexión, reconexión, limitador de peticiones; traducción de tipos IBKR → modelos de dominio. Interfaz `BrokerGateway` (protocolo) con implementaciones `IBKRGateway` y `FakeGateway` (tests).
- **Salida:** métodos: `get_account_summary()`, `get_positions()`, `get_contract_details(ticker)`, `get_option_chain(ticker)`, `get_quotes(contracts)`, `get_iv_history(ticker, desde)`, `get_underlying_price(ticker)`, `get_dividend_info(ticker)`, `what_if_margin(contract, qty)`, `get_vix_data()`.

### M3 — Persistencia (`storage`)
- **Entrada/Salida:** repositorios: `WatchlistRepo`, `TickerInfoRepo`, `IVHistoryRepo`, `ContractRepo`, `SnapshotRepo`.
- **Proceso:** SQLite con migraciones simples; solo capa de acceso a datos.

### M4 — Watchlist (`watchlist`)
- **Entrada:** texto pegado, o archivo .txt/.csv/.xlsx.
- **Proceso:** parseo (separadores coma, espacio, salto de línea, tabulador), normalización (mayúsculas), deduplicado, validación de formato.
- **Salida:** lista de tickers válidos + lista de rechazados con motivo. Marca como "pendientes de actualización diaria" los nuevos.

### M5 — Actualización diaria (`jobs/daily`)
- **Entrada:** watchlist.
- **Proceso (por ticker):** sector/categoría → expiraciones y DTE → strikes → contratos candidatos → días a ex-dividendo; IV Rank / IV Percentile de tastytrade (una petición en lote para todos).
- **Salida:** `TickerInfo`, `OptionContract` (candidatos), `IVHistory`; informe de errores por ticker.

### M6 — Refresco periódico (`jobs/refresh`)
- **Entrada:** contratos candidatos, X minutos.
- **Proceso:** obtener cotizaciones y griegas; calcular métricas (M7); solicitar margen what-if.
- **Salida:** `ContractSnapshot` con `updated_at`.

### M7 — Métricas (`metrics`, funciones puras)
- Spread %, Yield, Yield anualizado, IV Rank, IV Percentile (fórmulas en §8).
- **Entrada:** números; **Salida:** números o `None` si no computable.

### M8 — Scanner (`scanner`)
- **Entrada:** snapshots + `ScanCriteria` (% strike mín./máx., yield mín., DTE min/max, OI mín., spread máx., IV Rank mín., IV Percentile mín.).
- **Proceso:** filtrado con predicados componibles; enriquecimiento con M10 (peso sector, % si asignación).
- **Salida:** lista ordenable de `ScanResult`, más un motivo de descarte disponible en modo debug.

### M9 — Cartera y riesgo (`portfolio`)
- **Entrada:** resumen de cuenta, posiciones.
- **Proceso:** cushion y semáforo; Look Ahead / Post-Expiration; severidad.
- **Salida:** `RiskStatus` (valores + nivel de semáforo).

### M10 — Diversificación (`portfolio/diversification`)
- **Entrada:** posiciones (acciones + opciones abiertas), mapa ticker→sector.
- **Proceso:** % por sector actual; % por sector por semana de expiración (5 semanas); incremento por contrato candidato.
- **Salida:** `SectorExposure` actual y `WeeklyExposure[5]`.

### M11 — Simulador (`portfolio/simulator`)
- **Entrada:** cartera actual + lista de contratos seleccionados (con cantidad).
- **Proceso:** cartera hipotética asumiendo asignación de todos al strike; recalcula pesos y riesgo.
- **Salida:** `SimulationResult` (actual vs. futura, delta de sectores, delta de margen/cushion).

### M12 — Mercado VIX (`market`)
- **Entrada:** conexión broker.
- **Salida:** VIX actual, últimos 5 cierres, curva de futuros VIX (2-3 vencimientos).

### M13 — Planificador (`scheduler`)
- Lanza M5 (diario y bajo demanda para tickers nuevos) y M6/M9/M12 cada X minutos; evita ejecuciones solapadas.

### M14 — Interfaz (`ui`)
- Pestañas: Conexión/Watchlist, Scanner, Simulador, Riesgo, Diversificación. Sin lógica de negocio.

---

## 5. Arquitectura propuesta [PROPUESTA]

Arquitectura hexagonal ligera:

```
UI  ──►  Servicios de aplicación  ──►  Dominio (métricas, scanner, portfolio)
                    │
                    ├──► BrokerGateway (puerto) ◄── IBKRGateway / FakeGateway
                    └──► Repositorios (puerto)  ◄── SQLite
```

- **Lenguaje:** Python ≥ 3.11. **Cliente IBKR:** `ib_async` (sucesor mantenido de `ib_insync`) [PROPUESTA].
- **Validación/config:** `pydantic` + YAML. **Datos:** `pandas` solo en bordes (Excel, tablas UI); el dominio usa `dataclasses`.
- **UI:** (Q-02 aceptada). aplicación local con **FastAPI + Jinja2/HTMX** (evita los problemas de bucle asyncio de Streamlit con TWS). El núcleo no depende de la elección.
- **Concurrencia:** un único bucle asyncio propietario de la conexión IBKR; los jobs se ejecutan en él.
- **Reglas:** el dominio no importa `ib_async`; toda interacción con IBKR pasa por `BrokerGateway`; funciones de métricas puras.

## 6. Estructura de carpetas

```
ScannerOpcionesApp/
├─ README.md
├─ ESPECIFICACION_TECNICA.md
├─ pyproject.toml
├─ .env.example
├─ config/
│  ├─ config.example.yaml
│  └─ config.yaml                # ignorado por git
├─ data/                         # SQLite, logs (ignorado por git)
├─ src/scanner_opciones/
│  ├─ __init__.py
│  ├─ main.py                    # arranque / composición de dependencias
│  ├─ config/        settings.py
│  ├─ domain/        models.py  enums.py  errors.py
│  ├─ broker/        base.py  ibkr_gateway.py  ibkr_mapper.py  rate_limiter.py  fake_gateway.py
│  ├─ storage/       db.py  migrations.py  repositories.py
│  ├─ watchlist/     parser.py  loader.py  service.py
│  ├─ metrics/       spread.py  yields.py  iv_stats.py
│  ├─ scanner/       criteria.py  filters.py  engine.py  enrichment.py
│  ├─ portfolio/     risk.py  cushion.py  diversification.py  simulator.py
│  ├─ market/        vix.py
│  ├─ jobs/          daily_update.py  refresh.py  scheduler.py
│  └─ ui/            (según Q-02)
└─ tests/
   ├─ unit/          metrics, filters, parser, cushion, diversification, simulator, settings
   ├─ integration/   storage (SQLite en memoria), jobs con FakeGateway
   ├─ fixtures/      cadenas de opciones, historiales IV, watchlists de ejemplo
   └─ manual/        test_ibkr_smoke.py (requiere TWS, excluido de CI)
```

## 7. Configuración externa (`config.yaml`)

| Clave | Ejemplo / defecto | Notas |
|-------|-------------------|-------|
| `ibkr.host` | `127.0.0.1` | |
| `ibkr.ports.live` / `paper` | `7496` / `7497` | Puertos habituales TWS; Gateway usa 4001/4002 |
| `ibkr.client_id` | `1` | |
| `ibkr.accounts.live` / `paper` | — | id de cuenta por modo; obligatorio si TWS gestiona varias (si no, la primera) |
| `ibkr.auto_detect_mode` | `true` | al arrancar elige live/paper según el puerto que responda; si responden los dos o ninguno, usa `ibkr.mode` |
| `ibkr.mode` | `paper` | Por defecto **simulada** por seguridad [PROPUESTA] |
| `ibkr.market_data_type` | `2` (congelado: datos en vivo y, con el mercado cerrado, el último bid/ask del cierre; con `1` fuera de horario llegan vacíos) o `3` (diferido) | Q-11 resuelta; cambiado de `1` a `2` el 2026-10-01 |
| `ibkr.delayed_minutes` | `15` | Solo con `market_data_type` 3/4: el calendario de mercado se retrasa ese tiempo y el refresco automático no baja de ese intervalo (2026-10-01) |
| `refresh.interval_minutes` (X) | `5` | Q-08 resuelta |
| `daily_update.run_on_startup` | `true` | Al arrancar la app se ejecuta, **en segundo plano tras el primer refresco**, la actualización diaria si aún no se hizo hoy. **Sin hora fija** (Q-09) |
| `market.timezone` / `open` / `close` | `America/New_York`, `"09:30"`, `"16:00"` | Sesión regular de las opciones (la hora entre comillas en el YAML) |
| `market.holidays` | `[]` | Festivos de EE. UU. (a mano); los cierres anticipados no se modelan |
| `market.pause_when_closed` | `true` | Con el mercado cerrado: una captura y después solo cartera y VIX (RF-33). `false` = refrescar siempre |
| `logging.ib_async_level` | `WARNING` | Nivel del log de `ib_async` |
| `logging.tastytrade_level` | `WARNING` | Nivel del log del SDK de tastytrade (él mismo se fija en DEBUG y llenaría el log) |
| `logging.file` | `logs/scanner.log` | Fichero de log con rotación (`file_max_mb` 5, `file_backups` 3); `null` = solo consola |
| `daily_update.concurrency` | `4` | Tickers que se actualizan a la vez en la actualización diaria |
| `refresh.margin_max_age_minutes` | `60` | Antigüedad máxima del margen (what-if) guardado que se reutiliza sin volver a pedirlo |
| `scanner.candidates.*` | strikes `10`–`30` % por debajo, DTE `1`–`35` | Rango visible y cotizado (respecto al precio actual); el scanner solo ve contratos dentro de él |
| `scanner.catalog_margin_pct` | `5` | Puntos de más, por arriba y por abajo, que se GUARDAN respecto a `candidates` (RF-46) |
| `scanner.presets` | «Corto plazo»: 10 %, DTE 1–15, yield 20 %; «Largo plazo»: 20 %, DTE 16–máx. de la ventana (`dte_max: null`), yield 13 % | Botones del scanner que cargan esos valores y escanean; − / + de 1 en 1 junto a las cajas principales |
| `scanner.filter_values` | OI 100, bid size 20, spread 35 %, IV Rank 30, IV Percentile 50 | Valores de las cajas de los filtros opcionales cuando están desmarcados; marcar/desmarcar escanea |
| `scanner.initial.strike_below_pct_min` / `_max` | `10` / `30` | descuento mínimo y máximo del strike; valores iniciales editables |
| `scanner.initial.min_annual_yield_pct` | `12.0` | valor inicial editable |
| `scanner.initial.dte_min` / `dte_max` | `1` / `35` | valores iniciales editables (Q-03) |
| `scanner.filters.min_oi` / `min_bid_size` / `max_spread_pct` / `min_iv_rank` / `min_iv_percentile` | sin valor (filtro desactivado) | |
| `risk.cushion_thresholds` | `normal_above: 40`, `concern_above: 30` | verde >40 / ámbar 30-40 / rojo <30 (Q-05) |
| `diversification.weeks_ahead` | `5` | |
| `vix.history_days` / `vix.futures_ahead` | `5` / `3` | |
| `tastytrade.client_secret` / `tastytrade.refresh_token` | obligatorios | Credenciales OAuth de solo lectura de tastytrade (IV Rank / IV Percentile) |
| `storage.path` | `data/app.db` | |
| `logging.level` | `INFO` | |

## 8. Fórmulas [PROPUESTA salvo indicación; validar con Q-06/Q-07]

- **Spread %** = `(ask − bid) / mid × 100`, con `mid = (ask+bid)/2`. (Q-06: ¿sobre mid o sobre ask?)
- **Yield (gross premium yield)** [REQ: "prima por acción dividido entre el precio del strike"] = `prima / strike`. Prima = `mid = (bid+ask)/2` [Q-06 resuelta].
- **Yield anualizado** = `yield × 365 / DTE` (lineal, no compuesto). (Q-06)
- **IV Rank / IV Percentile:** los calcula tastytrade (rango de thinkorswim); la app solo los convierte de fracción a %.
- **IV Percentile** = `% de días de la ventana con IV < IV_actual`.
- **Cushion** = `ExcessLiquidity / NetLiquidation` (definición IBKR; se puede leer directamente el tag `Cushion` del account summary).
- **Cushion actual** = el tag `Cushion` de IBKR (fracción ×100), sin recalcular. **Cushion Look Ahead / Post-Expiration** [Q-05b] = `Excess Liquidity del escenario / NetLiquidation`.
- **Exposición potencial total (EPT)** [Q-10] = `Σ valor de mercado de las acciones` + `Σ exposición nominal de puts vendidas` (nominal = `strike × multiplicador × nº contratos`). Es la **base de todos los pesos**.
- **Peso sector** = `(valor acciones del sector + nominal puts vendidas del sector) / EPT`.
- **% cartera si asignación** = `nominal del contrato candidato / (EPT + nominal del candidato)` (el candidato se suma a la EPT al ser una nueva exposición).
- **Incremento peso sector** = `(exp_sector + nominal_candidato) / (EPT + nominal_candidato) − exp_sector / EPT`.
- **Peso respecto a la misma semana** [Q-10b] = `nominal del candidato / (nominal total de puts vendidas abiertas con expiración en esa semana + nominal del candidato)`. Semana = semana natural (lunes-domingo) de la fecha de expiración.
- **Distancia strike** = `(precio − strike) / precio × 100`.
- **DTE** = días naturales hasta expiración.

## 9. Modelos de dominio (campos mínimos)

- `TickerInfo`: ticker, sector, categoría, precio subyacente, días a ex-dividendo (nullable), iv_rank, iv_percentile, updated_daily_at.
- `OptionContract`: ticker, expiración, strike, right (P/C), multiplicador, conId, DTE.
- `ContractSnapshot`: contrato + bid, ask, last, delta, iv, oi, spread_pct, yield, yield_annualized, iv_rank, iv_percentile, initial_margin, `updated_at`.
- `Position`: instrumento, cantidad, valor de mercado, sector, expiración (si opción).
- `RiskStatus`: cushion, look_ahead, overnight, post_expiration, nivel semáforo por cada uno.
- `ScanCriteria`, `ScanResult`, `SectorExposure`, `SimulationResult`.

## 10. Comportamiento y manejo de errores

1. Un ticker que falla **no** detiene el lote; se registra el error y se muestra en la UI con motivo.
2. Datos nulos (bid/ask ≤ 0, NaN): el contrato queda **excluido del scanner** y se cuenta en un aviso.
3. Pérdida de conexión: reintento con backoff; la UI muestra estado "Desconectado" y datos con su timestamp.
4. Al cambiar real ↔ simulada: cerrar conexión, limpiar estado de cuenta en memoria y **no mezclar** datos de portfolio (separar en BD por `account_id`).
5. Fichero de watchlist inválido: error legible con la línea/celda problemática.
6. El refresco periódico no puede solaparse con el anterior; si tarda más de X min se omite el siguiente ciclo y se avisa.
7. Semáforo: si falta el dato de cushion, estado "Desconocido" (gris), nunca verde.

## 11. Tests automatizados

**Obligatorios (unitarios, sin TWS):**
- `metrics/*`: spread, yield, anualizado, IV Rank/Percentile (casos límite: max=min, historial corto, IV nula).
- `scanner/filters`: cada predicado por separado, combinaciones, valores en el límite (20% exacto, yield 1% exacto).
- `watchlist/parser`: separadores mezclados, duplicados, minúsculas, vacíos, Excel con cabecera / sin cabecera.
- `portfolio/cushion`: umbrales y fronteras (incluye huecos de Q-05).
- `portfolio/diversification` y `simulator`: pesos suman 100%, asignación de varios contratos, semanas sin contratos.
- `config/settings`: validación, campos faltantes, valores fuera de rango.
- `broker/rate_limiter`.

**Integración (con `FakeGateway` + SQLite en memoria):** actualización diaria incremental de IV (solo pide días nuevos), añadir ticker tras la ejecución diaria, ciclo de refresco completo, error en un ticker.

**Manual (requiere TWS paper):** smoke test de conexión, cadena de opciones, what-if, historial IV, VIX. Excluido de CI.

## 12. Notas de implementación IBKR [PROPUESTA — verificar contra la documentación de la API]

- Sector/categoría: `reqContractDetails` → `industry`, `category`, `subcategory`.
- Historial IV de IBKR: ya no se usa (IV Rank/Percentile vienen de tastytrade).
- OI y griegas: ticks genéricos (p. ej. 101 para OI de opciones) y `modelGreeks`.
- Ex-dividendo: tick genérico 456 (IB Dividends) o datos fundamentales (Q-12).
- Margen inicial: orden *what-if* (`whatIfOrder`, campos `initMarginChange`). **No es aditivo** entre contratos; se aproxima sumando (Q-13 resuelta).
- Cushion y liquidez: `accountSummary` / `accountValues` (`Cushion`, `LookAheadExcessLiquidity`, `LookAheadInitMarginReq`, `LookAheadMaintMarginReq`, `PostExpirationExcessLiquidity`, `PostExpirationMargin`). El valor "Overnight" **sí está expuesto** en la API según el usuario (Q-05b): localizar el tag exacto en la documentación; no inventarlo.
- VIX: índice `VIX` (CBOE) + futuros `VX` (CFE); requieren suscripciones (Q-11).

---

## 13. PENDIENTES — información faltante, ambigüedades y contradicciones

| ID | Punto | Texto original / problema | Pregunta concreta | Defecto provisional |
|----|-------|---------------------------|-------------------|---------------------|
| Q-01 | Tipo de operación | **RESUELTA**: solo puts vendidas; la app nunca envía órdenes (solo what-if). | — | — |
| Q-02 | Plataforma/UI | No se indica tipo de aplicación. | ¿Web local, escritorio o Excel/terminal? ¿Preferencia de lenguaje? | Python + FastAPI/HTMX local — **ACEPTADO por el usuario** |
| Q-03 | DTE | **RESUELTA** (matizada el 2026-10-01: ya no hay perfiles; el DTE inicial es 1–35; la columna «Operación» que marcaba Regular 25–35 y Táctica el resto se eliminó el 2026-10-06):  Regular 25-35 (configurable). Táctica: máximo 15 días, configurable. Se interpreta como DTE ≤ 15 (inclusive) y sin mínimo salvo DTE ≥ 1, salvo que se indique lo contrario. | — | — |
| Q-04 | % strike | **RESUELTA (2026-09-29; el 2026-10-01 pasa a un único filtro con descuento mín. 10 % y máx. 30 % editables)**: el descuento es un valor **mínimo** (strike al menos X % por debajo): Regular 20 %, Táctica 10 %, ambos editables. El máximo es el límite de lo guardado (40 %). Se guardan strikes de −10 % a −40 % (−45 % hasta 2026-10-01; antes −15 %, cambiado para que la Táctica al 10 % tenga datos) y DTE hasta 45 (60 hasta 2026-10-01). | — | — |
| Q-05 | Semáforo Cushion | **RESUELTA**: >40% Normal (verde); 30%-40% Preocupación (ámbar); <30% Riesgo alto (rojo). Fronteras confirmadas: 40% exacto → ámbar, 30% exacto → rojo. | — | — |
| Q-05b | Look Ahead / Overnight / Post-Expiration | **RESUELTA (2026-09-29)**: Overnight no existe en la API, se sustituye por `HighestSeverity` (0 Normal verde, 1 Advertencia/margen bajo ámbar, 2 Riesgo elevado/cerca del límite naranja, 3 Liquidación/margen crítico rojo; el usuario escribió "0 Normal rojo", se interpreta como verde, y no indicó color para 2 y 3). Cushion actual = tag `Cushion` de IBKR. Look Ahead y Post-Expiration = `Excess / NetLiquidation`. **Regla**: `PostExpirationExcess` = 0 se trata como "Sin datos" (gris). **Observado**: en la cuenta simulada `HighestSeverity` no llega (se muestra "Sin datos") y `PostExpirationExcess` vale 0. | — | — |
| Q-06 | Definición de yield | **Parcialmente resuelta**: prima = mid (bid+ask)/2. Se mantienen provisionales: anualizado lineal (×365/DTE); spread % sobre mid; "Max Spread" en %. | ¿Confirmas los tres provisionales? | lineal; mid; % — **ACEPTADO por el usuario** |
| Q-07 | IV Rank / Percentile | ¿Ventana 252 días de trading o 365 naturales? ¿IV del subyacente (30d constante) de IBKR? Se usa el mismo valor para todos los contratos del ticker. | Confirmar | 1 año, IV del subyacente — **ACEPTADO por el usuario** |
| Q-08 | "Cada X minutos" | **RESUELTA**: X = 5 min por defecto, configurable (`refresh.interval_minutes`). Se incluye botón de refresco manual [PROPUESTA]. | — | — |
| Q-09 | Ejecución diaria | **RESUELTA**: sin hora determinada. La actualización diaria se ejecuta al arrancar la app si no se ha hecho ya hoy, y bajo demanda (botón manual y al añadir tickers nuevos, RF-05). El planificador no dispara la actualización diaria a una hora fija. | — | — |
| Q-10 | Base de pesos | **RESUELTA**: base = Exposición Potencial Total (EPT) = valor de acciones + exposición nominal de puts vendidas (§8). Se ignora cash. | — | — |
| Q-10b | Peso respecto a la misma semana | **RESUELTA**: nominal del candidato / nominal total de puts vendidas de esa semana (incluyendo el candidato). | — | — |
| Q-11 | Suscripciones y datos | **RESUELTA**: opciones en tiempo real; VIX y futuros VIX pueden ser diferidos. Si faltan permisos, error explícito por ticker. | — | — |
| Q-12 | Ex-dividendo, sector | "Sector" y "Categoría": ¿los de IBKR (industry/category)? ¿Fuente para ex-dividendo? | Confirmar uso exclusivo de IBKR | Solo IBKR — **ACEPTADO por el usuario** |
| Q-13 | Simulación de margen | **RESUELTA**: se acepta una **aproximación** del margen total (suma de what-if individuales por contrato, marcada como "aproximado" en la UI). | — | — |
| Q-14 | Simulación de asignación | "Si se ejercieran" esos contratos. | ¿Se asume asignación al 100% de todos, con qué cantidad de contratos (input del usuario)? ¿Se calcula también escenario de caída de mercado? | 100% asignación, cantidad = input — **ACEPTADO por el usuario** |
| Q-15 | Diversificación 5 semanas | "Próximas 5 semanas en las que se tengan contratos abiertos": ¿5 semanas naturales desde hoy, o las 5 próximas que tengan contratos? | Aclarar. Y qué métrica se muestra por semana (nocional de asignación por sector). | 5 semanas naturales; nocional — **ACEPTADO por el usuario** |
| Q-16 | VIX futuros | "Valor previsto de futuros 2-3 semanas": los futuros VX son mensuales/semanales. | ¿Cuántos vencimientos y cuáles (próximos 2-3 disponibles)? | Próximos 3 vencimientos — **ACEPTADO por el usuario** |
| Q-17 | Volumen de datos | Tamaño de la watchlist y nº de contratos candidatos por ticker no indicados; el límite de líneas de mercado (por defecto 100) puede impedir refrescar todo en streaming. | ¿Cuántos tickers (~)? ¿Límite de contratos candidatos por ticker? | Snapshots por lotes; límite configurable — **ACEPTADO por el usuario** |
| Q-18 | Moneda | Cuenta irlandesa: moneda base posible EUR con subyacentes USD. | ¿Moneda base de la cuenta y conversión a mostrar? | Usar moneda base de la cuenta y convertir con FX de IBKR — **ACEPTADO por el usuario** |
| Q-19 | Idioma | Interfaz. | ¿Español? | Español — **ACEPTADO por el usuario** |
| Q-20 | Alertas | No mencionadas. | ¿Se desean notificaciones (p. ej. cushion <25%)? | No (fuera de alcance) — **ACEPTADO por el usuario** |
| Q-21 | Exportación | No mencionada. | ¿Exportar resultados a Excel/CSV? | Fuera de alcance (ampliación futura) — **ACEPTADO por el usuario** |

**Contradicciones/erratas detectadas:** DTE "25 a 25"; DTE táctica sin número; hueco 30-40% en semáforo; "Riesgo algo"; "Overnigh"/"Post-Expirity" (se interpretan como *Overnight* y *Post-Expiration*); "exdividendo" (ex-dividend); "L20: guaradado".

### Fuente de IV Rank / IV Percentile (tastytrade)
- Puerto `VolatilityProvider` (`marketdata/volatility.py`); implementación real `TastytradeVolatility` (`marketdata/tastytrade.py`) con OAuth de solo lectura; `FakeVolatility` en tests.
- Valores distintos a los calculados antes con el historial de IBKR (decisión del usuario 2026-10-06: usar el rank de tastytrade y eliminar el respaldo de IBKR).
- Se conserva la tabla `iv_history` sin usar (no se borra para no perder datos).

### Datos de mercado por tastytrade (rama `tastytrade-datos`, 2026-10-08)
- `market_data.source: tastytrade` (por defecto en esta rama) saca de tastytrade la cadena (puts que existen de verdad, sin `qualify_contracts`), las cotizaciones de opciones (DXLink `Quote`/`Greeks`/`Summary`), los precios de los subyacentes, la IV a 30 días y el ex-dividendo. La cuenta, las posiciones, el margen what-if, el sector y el VIX siguen siendo de IBKR. `source: ibkr` recupera el comportamiento anterior; esa versión también está en la etiqueta git `version-ibkr-datos`.
- Implementación: puerto `OptionDataProvider` (`marketdata/options.py`), métodos nuevos de `TastytradeVolatility` y `HybridGateway` (`broker/hybrid_gateway.py`), que envuelve a `IBKRGateway`. Si el proveedor falla, cada consulta cae a IBKR con un aviso en el log.
- Los contratos guardados ya no traen `con_id` (lo resuelve IBKR al pedir el margen what-if). Los antiguos lo conservan.
- Medido el 2026-10-08 (prueba `tests/manual/test_tastytrade_options_vs_ibkr.py`): mismos contratos que IBKR en 586/586; cadena + validación ~2,5 s frente a ~50 s para 6 tickers; open interest y bid size idénticos. Con el mercado abierto (2026-10-08): cobertura de tastytrade 100 % frente a ~50–60 % de IBKR; diferencia mediana de bid/ask 0, delta 0,001, IV ~0,03 (tastytrade algo mayor), OI idéntico. Antes quedaba pendiente: IV, delta, bid y ask en vivo. El VIX y sus futuros también vienen de tastytrade (Yahoo no tiene la curva de futuros; descartado). El what-if de margen se mantiene en IBKR: contrastada con 2.855 márgenes reales, la fórmula «prima + 20 % del precio − OTM / prima + 10 % del strike» tiene un error mediano del 27 % y subestima 75–88 % en valores volátiles (LITE, MU, SNDK…). Cuenta/posiciones/VIX se refrescan aparte del ciclo de mercado (`refresh.account_interval_minutes`). Diferencias conocidas: el bid sin ofertas llega como 0 (IBKR lo daba como «sin dato»); `last` no se rellena; el ex-dividendo es la última fecha conocida de tastytrade (si ya pasó, «sin dividendo próximo»).

### Filtros de calidad de la empresa (2026-10-08)
- Petición del usuario: algunos tickers de las listas de calidad o crecimiento tienen beneficios negativos. Se añade el bloque «Calidad de la empresa» al scanner con: beneficios en 12 meses, trimestres con beneficios (de los últimos 4), capitalización mínima, liquidez de las opciones y «evitar vencimientos con resultados». *Cambio 2026-10-09 (petición del usuario):* se quita el filtro de capitalización mínima del Scanner y del Universo; la capitalización queda solo como columna. Decisión del usuario: el filtro de resultados es **solo por contrato** (no por ticker): en temporada de resultados un filtro por ticker eliminaría casi toda la watchlist (86 % publica en 35 días).
- Fuente: tastytrade (sin dependencias nuevas). Medido sobre 475 tickers: EPS ≤ 0, 34 (7 %); menos de 3 de 4 trimestres positivos, 48 (10 %); capitalización < 2 B$, 23 (4 %). Cuidado con el EPS de tastytrade: BNY y CB venían con `-99999,99` / `0,0` siendo rentables y SNDK sin dato; se corrigen con la suma de los 4 últimos trimestres. La liquidez de opciones < 3 afecta al 64 % de los tickers: se ofrece como filtro suave (2-4).
- **Fase 2 (hecha, 2026-10-08): apalancamiento y flujo de caja con SEC EDGAR.** Se descartó HelloStocks (solo cubre el 40 % de la watchlist en deuda/patrimonio y el 37 % en flujo de caja libre). Métricas: pasivo total / patrimonio (uniforme para todas las empresas; la «deuda financiera» depende de etiquetas muy variables) y flujo de caja libre de 12 meses. Filtros: «Pasivo / patrimonio máximo» (1, 2, 3 o 5) y «Flujo de caja libre positivo»; las financieras (bancos, aseguradoras) quedan exentas. Los tickers sin dato se descartan (ADR y empresas con patrimonio negativo, p. ej. SBUX). Requiere `edgar.contact` (la SEC exige un contacto en el User-Agent); sin él los filtros no tienen datos. Medido: 0,22 s por empresa (475 tickers en ~2 min), cobertura 27/30 con algún dato y 23/30 con ambos en una muestra variada.

### Filtros de calidad en el Universo, antes de la watchlist (2026-10-08)
- Petición del usuario: el filtrado de calidad debe hacerse **antes** de incluir los tickers en la watchlist, en la vista donde se ven todas las acciones cargadas, con columnas de indicadores y con la posibilidad de pasar a la watchlist solo las que cumplan.
- La pestaña Universo tiene ahora un panel «Filtros de calidad» (beneficios en 12 meses, trimestres con beneficios, pasivo/patrimonio y flujo de caja libre; el filtro de resultados queda en el Scanner por ser por contrato y, por decisión del usuario, el de liquidez de opciones también: en el Universo la liquidez es solo una columna) y las columnas EPS 12 m, Trim. +, Cap. (B$), Liq. opc., Resultados, Pasivo/Patr. y FCF (M$), ordenables, con los valores problemáticos en rojo. Los filtros viajan en la URL, se validan contra las listas de `scanner.quality` y se pueden combinar con la fuente elegida. Las filas visibles son las que quedan marcadas: «Añadir selección» o «Sustituir watchlist con la selección» pasan solo esas.
- Para ello los datos de calidad se descargan para todo el Universo (455 tickers hoy, ~2 min la primera vez) y se guardan en una tabla propia, `ticker_quality`. El Scanner conserva sus filtros de calidad, y es el único que tiene el de resultados antes del vencimiento.

### Solvencia con grados de exigencia y sectores exentos (2026-10-08)
- Petición del usuario, a raíz de un análisis de un experto (filtros de flujo de caja y de deuda): aplicar todas las opciones propuestas al Universo, con varios grados de exigencia seleccionables.
- **Filtros de solvencia** (bloque con selector maestro de grado): deuda financiera/patrimonio, cobertura de intereses, efectivo/deuda a corto plazo y flujo operativo/deuda. **Indicadores opcionales** (sin filtro por defecto): CapEx/flujo operativo, FCF/activos y recompra neta. Cada uno con tres grados (Flexible, Estándar, Estricto) cuyos umbrales viven en `scanner.quality.thresholds`; «Estricto» es el umbral más duro del experto (deuda/patrimonio ≤ 0,5; cobertura ≥ 5×… el suyo era ≥ 3×, que aquí es «Estándar»; FCF/activos ≥ 12 %; recompra neta ≥ 2 %; CapEx < 20 %, más estricto que su 35 %, que es «Estándar»).
- **Sectores exentos** (decisión del usuario): financiero, energía, utilities, materiales e inmobiliario no se miden en pasivo/patrimonio, solvencia ni caja (`scanner.quality.exempt_sectors`). Son un tercio del Universo (150 de 452: Financial 88, Energy 35, Basic Materials 20, Utilities 7): se ven como «n/a».
- Medido sobre el Universo con los umbrales del experto (291 aplicables): flujo operativo > 30 % de la deuda, 66 %; CapEx < 35 %, 71 %; recompra neta > 2 %, 28 %; FCF > 12 % de activos, 39 %; deuda/patrimonio ≤ 0,5, 44 %; < 1,0, 69 %; efectivo > deuda corriente, 83 %; cobertura ≥ 3×, 79 %. Combinar los cuatro de caja deja solo 22: demasiado estricto para una watchlist, de ahí los grados.
- Las cifras de rentabilidad del experto (p. ej. 86 % a 5 años) son sus backtests y no se han verificado; sirven la lógica de los filtros, no los porcentajes. La métrica «Pasivo/Patr.» (pasivo total) no es comparable a su Debt/Equity (deuda financiera): mediana 1,52 frente a 0,56 en el Universo.

### Tickers inservibles (2026-10-08)
- Petición del usuario: los tickers que dan problemas permanentes (ANSS: IBKR no lo reconoce; UI: sin cadena de opciones) no deben estar en la watchlist. `UnsupportedTickerError` (subclase de `DataUnavailableError`) solo se lanza para esos dos casos; la actualización diaria los saca de la watchlist, borra sus contratos y ficha, y lo avisa una sola vez (ticker y motivo) con un aviso que se cierra con una «×». Los fallos pasajeros se siguen tratando como error normal. *Cambio 2026-10-09 (petición del usuario):* la exclusión ya no es permanente ni se lista en la pestaña Watchlist: no se impide volver a añadirlos (si siguen sin servir, se quitan y se avisa de nuevo).

### Universo, rediseño y Contratos (2026-10-06)
- El ticker de cada fila se corta en el primer `-` o `.` (`PBR-A` → `PBR`, `BRK.B` → `BRK`). Si un fichero guardado está corrupto se ignora al arrancar.
- Se retira la pestaña Contratos. La pestaña RankedStocks pasa a ser **Universo** (RF-37). Menú: Panel · Universo · Watchlist · Scanner.
- Rediseño: tema cálido (crema/azul marino), tarjetas numeradas agrupadas por secciones (Contrato, Tendencia y niveles, Liquidez y volatilidad) con nota explicativa con barra ámbar, como referencia `imagenes/Trendandlevels.png` y `Min_Days_Since_Last_Touch.png`; los ids y nombres del formulario del scanner no cambian.

### Símbolos con clase de acciones (2026-10-06)
- Al hablar con IBKR, `.` y `-` del ticker se sustituyen por un espacio (`BRK.B` → `BRK B`); el ticker guardado no cambia. Pendiente de verificar contra TWS con opciones de esas clases.

## 14. Ampliaciones futuras previstas (no implementar ahora)

Nuevas estrategias en el scanner (calls cubiertas, spreads) añadiendo `ScanCriteria`/filtros sin tocar el motor; otros brokers vía nueva implementación de `BrokerGateway`; alertas; exportación; más escenarios de simulación (estrés de mercado).

## 15. Orden de implementación sugerido

1. `config`, `domain`, `metrics` + tests.
2. `watchlist` + tests.
3. `storage`.
4. `broker` (interfaz + `FakeGateway`), luego `IBKRGateway`.
5. `jobs/daily_update`, `jobs/refresh`.
6. `scanner`.
7. `portfolio` (riesgo, diversificación, simulador).
8. `market/vix`.
9. UI.
10. Pruebas manuales con TWS simulada.

**Criterio de "hecho" por módulo:** tests unitarios en verde, sin acceso a red en tests unitarios, errores devueltos con mensajes claros, cero valores de negocio hardcodeados.
