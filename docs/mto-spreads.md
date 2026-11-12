# MTO: Put Credit Spread ITM y Call Debit Spread muy OTM (análisis 2026-11-02)

Análisis de `estrategia/Estrategias MTO.txt` y `estrategia/logbook_mto_2026-11-02.csv` (550 patas, cuentas 5K/10K/50K, 2025-11 a 2026-11) para definir un screener de las dos estructuras y sus salidas. **Es un análisis de operaciones pasadas, no una recomendación de inversión.** Los screeners de «Propuesta» están pendientes de implementar; las salidas ya están en el perfil MTO (ver «Salidas implementadas»).

## Método y límites

- **Spreads:** se reconstruyen uniendo patas por cuenta, ticker, hora de apertura y vencimiento (62 PCS y 53 CDS; 52 PCS y 49 CDS tras limpiar patas sueltas, ETHA, VIX y estructuras no verticales).
- **Precio del subyacente:** cierres diarios de la base de datos de la app (tastytrade/IBKR); BITO, CELH, CONL, EOSE, ETHA, HIMS, OPEN y SCHD (y el VIX) salen de Yahoo (`yfinance`, solo para este análisis, sin guardar nada). Se toma el cierre del día de apertura, no el precio intradía exacto.
- **ETHA se excluye**: sus strikes (15–19) no cuadran con el precio de Yahoo (~50).
- **Trayectoria:** no hay precios históricos de opciones. El valor del spread cada día es `precio real de entrada + (Black-Scholes_t − Black-Scholes_0)`, con la volatilidad realizada a 60 días del subyacente. Validación contra los cierres reales: CDS correlación 0,96 y error mediano 5 % del riesgo; PCS correlación 0,88 y error mediano 9 %. Sirve para **comparar reglas entre sí**, no para dar un P&L exacto.
- **Muestras pequeñas** (46 PCS y 28 CDS cerrados) y concentradas (TSLL, CELH, RDDT, NVDA). Los tramos de unas pocas operaciones no son concluyentes.

## Corrección clave: el CSV no recoge la pata de acciones

En 17 de los 46 PCS cerrados el subyacente cerró **por debajo de ambos strikes** al vencimiento (ASTS 85 con 125/130, NFLX 88 con 103/105, TSLL 9,8 con 13/14…). El CSV los da como «Expirada» a precio 0 y con el crédito entero como beneficio: es una **asignación registrada como expiración**, sin el resultado de las acciones.

| | Nº | P&L del CSV | P&L económico del spread* | Aciertos |
|---|---|---|---|---|
| PCS cerrados antes del vencimiento o sin asignar | 29 | +770 $ | +738 $ | 86 % |
| PCS asignados / ITM al vencer | 17 | +6.095 $ | −1.468 $ | 6 % |
| **Total** | 46 | **+6.865 $** | **−730 $** | 57 % |

\* Valor intrínseco al vencer de ambas patas (pérdida acotada por la put comprada), sin comisiones. No incluye lo que dé después la rueda con las acciones.

Por tanto: el «90 % de aciertos y +8.022 $» de una primera lectura del CSV **no es válido** como resultado del spread.

### La rueda sí se puede estimar

El CSV no trae las acciones, pero sí las calls cubiertas vendidas después. Se reconstruye así: resultado = P&L del spread (con la put comprada vendida, si se vendió) + primas netas de las calls cubiertas + acciones (compradas al strike vendido; vendidas al strike de la call si la ejercieron, o marcadas al último cierre si no). Contraste con los números del propio texto: **PATH +225 $ (texto: +224,72 $) y CELH +13 $ (texto: 1.763 $ de primas frente a 1.750 $ de caída)**.

| Grupo (17 PCS asignados) | Nº | Spread puro | Con rueda |
|---|---|---|---|
| Con calls cubiertas posteriores (CONL, PATH, PFE ×2, SNAP, ZETA, CELH, TSLL) | 8 | −561 $ | **≈ +3.200 $** (+13 a +1.048 $ cada una) |
| Sin calls (ASTS, HOOD, NFLX, SOUN, TLT, UBER ×2, META) | 8 | −907 $ | −907 $ (se asume que se cerraron como spread; mantener las acciones habría costado entre −1.000 y −8.900 $ cada una) |
| OPEN | 1 | −11 $ | −495 $ (dos calls y acciones marcadas a 1,80 $) |
| **Total** | 17 | −1.468 $ | **≈ +1.800 a +2.300 $** |

Con la rueda, los 46 PCS cerrados pasan de −730 $ a **≈ +1.100 a +1.600 $**.

Salvedades:
- **Sesgo de selección:** se hizo la rueda en los valores que luego se recuperaron (PFE, ZETA, TSLL) y se cortó en los que no (ASTS, HOOD, NFLX…). Es un resultado con visión retrospectiva; la regla «rueda solo en empresas de calidad» tendría que saber elegir de antemano.
- Las acciones no vendidas (SNAP, TSLL) se marcan al cierre del 2026-10-30. Se ignoran dividendos (PFE ~86 $) y comisiones de acciones.
- La asignación de calls cubiertas por ticker/cuenta/fecha puede confundirse con calls de otras estrategias en el mismo valor; se filtraron por strike cercano al de asignación y se revisaron CELH y PATH a mano.
- Las acciones de ASTS, META, NFLX o HOOD no se incluyen aunque se hubieran conservado: el cálculo exige que haya evidencia de la rueda en el CSV.

## Resultados por cuenta

El logbook mezcla tres cuentas: **10K** (275 patas, 51 tickers), **50K** (254 patas, 51 tickers) y **5K** (21 patas: TSLL, BITO, SCHD, PBR; es la de ETFs). Lo que sigue repite el análisis por cuenta. Las muestras por cuenta son pequeñas: las diferencias entre 10K y 50K no son estadísticamente concluyentes.

| Cuenta | PCS cerrados | Spread puro (eco) | Con rueda | CDS cerrados (real) | CDS abiertos (modelo) | CDS neto | CDS con reglas (+20 / −70 / 90 DTE) |
|---|---|---|---|---|---|---|---|
| **10K** | 33 (13 asignados) | −617 $ | **+819 $** | +670 $ (70 % aciertos) | −815 $ (10 abiertos) | −145 $ | +363 $ |
| **50K** | 11 (4 asignados) | −133 $ | **+1.690 $** | +1.883 $ (17 de 17) | −2.475 $ (12 abiertos) | −592 $ | −11 $ |
| **5K** | 2 | +20 $ | +20 $ | +54 $ (1 operación) | — | +54 $ | +33 $ |

- **Resultado total por cuenta (PCS con rueda + CDS neto):** 10K ≈ +674 $ (+6,7 % de la cuenta), 50K ≈ +1.098 $ (+2,2 %), 5K ≈ +74 $ (+1,5 %). Con PCS y CDS ocupando una parte pequeña del capital.
- **50K:** la rueda explica todo el resultado de los PCS (CELH +13, PFE ×2 y TSLL ≈ +1.500 $). Sus 17 CDS cerrados son todos ganadores (movimientos rápidos de grandes empresas), pero los 12 abiertos (5.433 $ de riesgo, un 11 % de la cuenta) se marcan al −46 % del riesgo.
- **10K:** los PCS pasan de −617 $ a +819 $ solo por la rueda (ZETA +920, SNAP +254, PATH +225, PFE +174, CONL +107, OPEN −495). Sus 10 CDS abiertos (1.587 $ de riesgo) más los PCS abiertos suponen **~20 % de la cuenta en riesgo definido**, la concentración mayor.
- **5K (ETFs):** solo 2 PCS y 1 CDS; no permite sacar conclusiones. Su P&L viene de TSLL con puts desnudas (+386 $), fuera de estas dos estrategias.
- **Fuera de alcance, pero relevante por cuenta:** las Long Put suman −9.816 $ (10K) y −4.344 $ (50K), más que lo que dan los PCS y CDS juntos; conviene saber si son coberturas.
- En ambas cuentas el backtest de reglas da lo mismo que en conjunto: mantener hasta vencimiento pierde (PCS 10K −829 $, 50K −679 $; CDS 10K −303 $, 50K −5.893 $), y las salidas rápidas recortan la pérdida (CDS 10K +363 $, 50K −11 $).
- La mejor cuenta no es la mejor estrategia: en 50K los CDS son casi todo ganancia realizada y pérdida latente; en 10K es al revés en proporción.

## 1. Put Credit Spread ITM

**Lo que sí se sostiene con los datos:**

- **Cerrar pronto funciona; esperar al vencimiento no.** Cerrados en ≤ 30 días: 86 % de aciertos. Los que pasan de 30 días: 28 % de aciertos y −1.041 $.
- **El filtro de tendencia es el que más separa:** con el precio > 10 % por debajo de la media de 200 sesiones (22 operaciones): 77 % de aciertos y +305 $; el resto (24): −1.035 $.
- **Profundidad ITM (strike vendido sobre el precio):** ≤ 2 %: 44 % de aciertos; 15–50 %: 78 %. La relación es débil (5–10 % también salió floja: 42 %).
- **DTE:** 45–60 y > 60 días: 75 % de aciertos; ≤ 45 días: 38–44 % y pérdidas.
- **Crédito/ancho** no discrimina una vez corregida la asignación (0,5–0,7: 56–64 % de aciertos).

**Backtest de reglas de salida** (52 PCS, mark diario, deslizamiento 2 % del ancho):

| Regla | Aciertos | P&L modelo | ROI medio sobre riesgo |
|---|---|---|---|
| Mantener hasta vencimiento | 31 % | −1.460 $ | −30 % |
| Cerrar siempre a 21 DTE | 44 % | −94 $ | −2 % |
| Objetivo +60 % del riesgo + stop −70 % | 50 % | −261 $ | −2 % |
| Objetivo +40 % + stop −50 % + 21 DTE (solo vs200 < −10 %) | 52 % | −117 $ | −3 % |
| Real (decisiones del operador, cerrados) | 57 % | −730 $ | −8 % |

Ninguna regla mecánica sale claramente positiva: el spread, **como instrumento aislado, es aproximadamente neutro** en este periodo. Lo que lo vuelve ligeramente positivo es la rueda de acciones tras la asignación (ver arriba, ≈ +1.800 a +2.300 $ sobre los 17 asignados), que depende de elegir bien en qué valores quedarse con las acciones.

### Propuesta: screener «MTO PCS ITM»

| Filtro | Valor | Respaldo |
|---|---|---|
| Strike vendido | En el dinero o por encima del precio, mejor ≥ 2–5 % ITM | débil |
| Precio vs media 200 | ≥ 10 % por debajo | fuerte (22/22 con ROI mediano +71 %) |
| DTE | 45–60 | moderado |
| Ancho / riesgo | 1–2,5 puntos; riesgo ≤ ~150 $ por contrato | descriptivo |
| Calidad | La de la rueda: solo empresas con las que se quiera quedar las acciones | texto de MTO |
| Sin resultados dentro del DTE | sí | prudencia |

### Propuesta: salidas PCS ITM

1. **Cerrar con +40 % del riesgo** en ≤ 15 días (los cierres rápidos son los que ganan).
2. **Stop al −50 % del riesgo.** El stop heredado de tastytrade (200 % del crédito) **no puede saltar** aquí: con crédito/ancho 0,6 equivale a 1,2 veces el ancho, más que la pérdida máxima.
3. **Cierre a 21 DTE** si no hay objetivo; no mantener hasta el vencimiento salvo que se quiera la asignación.
4. **Asignación solo como decisión de rueda** (empresa de calidad); contabilizar aparte la pata de acciones.

## 2. Call Debit Spread muy OTM

**Resultado real vs marcas:** 28 CDS cerrados dieron +2.607 $ (89 % de aciertos, ROI mediano +21 %, mediana de 7 días). Los 21 abiertos, valorados con el modelo, llevan **−3.290 $** (riesgo 6.410 $; 13 por debajo de −60 % del débito). El conjunto es aproximadamente neutro.

| Corte (reglas: +20 % / stop −70 % / salir a 90 DTE) | Nº | Aciertos | P&L | Mantener |
|---|---|---|---|---|
| Todos | 49 | 59 % | +385 $ | −6.126 $ |
| DTE de apertura < 200 | 11 | 18 % | −763 $ | −1.420 $ |
| DTE de apertura 200–400 | 15 | 80 % | +655 $ | −1.318 $ |
| DTE de apertura 400–1000 | 23 | 65 % | +493 $ | −3.388 $ |
| Precio < SMA200 − 10 % | 27 | 70 % | +290 $ | −3.475 $ |
| Grandes empresas (NVDA, META, GOOGL, MSFT, AVGO, ADBE, BAC, MCD, PEP, DAL, UBER, NOW) | 21 | 71 % | +1.216 $ | −706 $ |
| Pequeñas y volátiles | 28 | 50 % | −830 $ | −5.420 $ |

- **El patrón ganador es un rebote rápido:** las 20 operaciones con subida > 0 % ganaron (ROI mediano +15–25 % en 4–11 días). Las 3 con caída perdieron el ~100 % (57 días de media, DTE < 75).
- **Distancia OTM de la call comprada:** no discrimina de forma clara (0–30 %: 47 % de aciertos con reglas; 30–60 %: 64 %; 60–130 %: 63 %).
- **Débito/ancho:** sin relación clara una vez aplicadas las reglas.
- **Mantener sin reglas pierde** (−6.126 $, 22 % de aciertos): sin la salida rápida, la estrategia no funciona.

**Grid de salidas (49 CDS, deslizamiento 3 % del débito):**

| Objetivo | Aciertos | P&L |
|---|---|---|
| +15 % | 65 % | −227 $ |
| +20 % | 59 % | −107 $ |
| +25 % | 53 % | −923 $ |
| +35 % | 43 % | −2.643 $ |
| +100 % | 24 % | −5.388 $ |
| +20 % con stop −70 % y salida a 90 DTE | 59 % | **+385 $** |

El objetivo corto (+15–20 %) con un stop y una salida por DTE es lo único que sale positivo. Un objetivo alto (+50–100 %) —el del texto— pierde en conjunto: los pocos ganadores grandes (MSFT +188 %, RDDT +89 %, ADBE +45 %) no compensan.

### Propuesta: screener «MTO CDS OTM»

| Filtro | Valor |
|---|---|
| Call comprada | Muy OTM (≥ ~25 % sobre el precio) y call vendida más arriba |
| DTE | **200–500**; descartar < 200 |
| Calidad | Grandes empresas líquidas; evitar pequeñas y muy volátiles |
| Tendencia | Precio ≥ 10 % por debajo de la SMA200 (retroceso, no ruptura) |
| Débito | ~150–650 $ por contrato |
| Límites | Una CDS abierta por ticker; riesgo total en CDS con tope explícito |

### Propuesta: salidas CDS OTM

1. **Objetivo +20 % sobre el débito** (no sobre el beneficio máximo).
2. **Stop −70 % del débito.**
3. **Salida por tiempo:** cerrar a ≤ 90 DTE si no se ha cumplido el objetivo.
4. **Pata corta:** recomprarla cuando valga ≤ 10–15 % de lo cobrado y se mantenga la tesis; sube la pérdida máxima por el coste de recompra.
5. **Posiciones abiertas:** revisar las que tienen ≤ 120 DTE y marca ≈ −100 % (EOSE, ASTS, RKLB, ONDS, ENPH, RIVN, IBIT); el valor residual es casi nulo.

## ¿El DTE largo sirve para esperar una recuperación? (2026-11-02)

Hipótesis: se eligen DTE tan lejanos para tener tiempo de que cualquier recuperación deje cerrar en positivo. Contraste con la trayectoria modelada (el pico de ROI es optimista: es el máximo de una serie con error, sin deslizamiento):

| CDS por DTE de apertura | Nº | Tocaron beneficio (>0) | Alcanzaron +20 % | Días a +20 % (mediana) | En pérdida > 20 % al día 30 → luego > 0 |
|---|---|---|---|---|---|
| < 200 | 11 | 55 % | 27 % | 16 | 8 → **0** |
| 200–400 | 15 | **100 %** | **80 %** | 6,5 | 3 → **3** |
| ≥ 400 | 23 | 83 % | 52 % | 7 | 4 → 2 |

- **Sí:** un DTE largo da opciones. Con 200–400 días las 15 tocaron beneficio en algún momento, y las 3 que iban en pérdida al día 30 acabaron en positivo. Con < 200 días ninguna se recuperó. En total, 5 de 15 CDS en pérdida al día 30 volvieron a positivo. En PCS, DTE > 45 acaba con −6 % de media frente a −52 % con ≤ 45.
- **Pero la ventana de salida es corta:** la mediana hasta +20 % son 7–8 días, y el 55 % de las operaciones lo alcanzó en algún momento. Esperar una recuperación posterior es la excepción (5 de 15), no la norma.
- **El coste de esperar:** de los 21 CDS abiertos, 8 llegaron a +20 % o más y ya no están ahí. EOSE +91 % → −100 %, ONDS +51 % → −70 %, NKE +44 % → −88 %, IBIT +31 % → −65 %, RIVN +22 % → −73 %. Los 6 que nunca fueron positivos (ASTS, RKLB, NFLX, GOOGL ×2, AVGO) tienen una tesis que aún no ha empezado.
- **Lectura:** el DTE largo funciona **si hay una regla que obligue a cerrar en el primer rebote** (+20 %). Sin esa regla, el tiempo se convierte en el riesgo: la mayoría de los que no cerraron en el pico devolvieron todo. Recuperar a cero (> 0) no cubre además comisiones ni deslizamiento.

## Salidas implementadas en el perfil MTO (2026-11-02)

`ExitRules` tiene tres campos nuevos: `risk_take_profit_pct` y `risk_stop_pct` (spreads verticales de crédito, % del **riesgo** = ancho − crédito) y `debit_gain_pct` (debit spread, % de lo **pagado**). Los aplican `management.put_status` (puts-spread) y `other_mgmt` (call credit spreads y debit spreads). El perfil de gestión MTO (`mgmt_profiles.MTO_EXITS`, sobre el estilo TPC) lleva:

| Estructura | Regla | Valor |
|---|---|---|
| PCS | Recoger | +40 % del riesgo en ≤ 15 días (`risk_take_profit_pct`, `risk_take_profit_days`) |
| PCS | Stop | −50 % del riesgo (`risk_stop_pct`) |
| PCS | Tiempo | revisar a ≤ 21 DTE (`review_dte`) |
| CDS | Recoger | +20 % sobre el débito (`debit_gain_pct`) |
| CDS | Stop | −70 % del débito (`long_stop_pct`) |
| CDS | Tiempo | revisar a ≤ 90 DTE (`long_review_dte`) |

Los avisos son orientativos. Una instalación que ya tenía MTO sin cambios recibe estas reglas una vez (`AppService.apply_mto_exits`, `meta.mig_mto_exits`); si MTO ya estaba editado no se toca.

Límites conocidos:
- `review_dte` es común a todas las ventas de opciones: las puts desnudas de MTO también se revisan a ≤ 21 DTE.
- El objetivo +40 % del PCS solo vale en los primeros 15 días desde la apertura (`risk_take_profit_days`; `put_status(opened_on=...)`, con la fecha de `AppService.put_open_dates`: la exacta de Tortuga o, si no está, la primera vez que la app vio la posición). Sin fecha, el objetivo vale. Pasado el plazo solo quedan el stop y la revisión a 21 DTE. Solo en puts-spread: los call credit spreads no llevan fecha.
- La pata corta del CDS (recompra a ≤ 10–15 % de lo cobrado) y la regla de asignación/rueda no están implementadas.

## Posiciones actuales y fechas de apertura: API de Tortuga (2026-11-02)

Las posiciones abiertas de MTO y su fecha exacta de apertura ya no se piden a IBKR Flex (retirado): salen de la **API de Tortuga** (`tortuga/api.py::TortugaApi.openings_document`, rutina `aperturas`, `tortuga.api_token`). Llama a `get_context` (cuentas) y a `list_trades` por cuenta (`status=open`/`closed`, `type=option`/`stock`, paginado) y escribe `community.openings_path`: puts vendidas abiertas (120 días), puts recompradas (90 días, con `pct_premium_captured` y `pnl`) y `others` (acciones, calls y puts compradas, 400 días; incluye las patas de combos, de ahí salen los CDS). `AppService.openings` lee ese fichero y `put_open_dates` da la fecha a `put_status(opened_on=...)`; sin fecha, la primera vez que la app vio la posición.

Lo que el análisis de este documento obtuvo del CSV del logbook (patas, precios de entrada, cierres) se puede pedir con las mismas herramientas (`list_trades`, `get_trade`, `get_portfolio`) para repetir el análisis sin exportar a mano. **No verificado:** la app solo usa `list_trades`; no se ha comprobado que la API HTTP exponga `get_portfolio`/`get_trade` con el mismo JSON que el conector, ni que `list_trades` traiga la pata de acciones tras una asignación (limitación del CSV, ver arriba).

## Motor del screener «MTO CDS OTM» (2026-11-02, solo el motor)

`scanner/cds.py::build_call_debit_spreads` (función pura, `tests/unit/test_cds.py`) y `scanner/candidates.py::call_candidate_contracts`, con los valores de `scanner.cds` (`CdsSettings`: DTE 200–500, comprada 25–130 % OTM, anchos 5/10/20/50 $, débito 150–650 $, OI ≥ 10 y spread ≤ 25 % en cada pata, SMA200 −10 %). Comprada al ask, vendida al precio de referencia, comisiones de las dos patas (`cost_ps`). Sin límite de riesgo total (decisión del usuario).

**Rentabilidad mínima esperada (filtro obligatorio):** movimiento necesario = `target_gain_pct` (20 %) × débito / delta neta (delta comprada − delta vendida), en % del precio; debe ser ≤ `max_required_move_mult` (1) × el movimiento esperado a `move_days` (10) días = IV × √(días/365), donde la IV es la de `move_days` días: se interpola en varianza total (IV²·T) entre los dos vencimientos de `expiry_ivs` (IV por vencimiento de tastytrade) que rodean ese plazo (2026-11-03; `scanner/cds.py::iv_at_horizon`; con un solo lado, el vencimiento más cercano; sin vencimientos, el IV30; sin IV30, la IV de la pata). Sin delta o sin IV se descarta. De apoyo: retorno máximo `(ancho − débito)/débito` ≥ `min_max_return_pct` (100 %). La distancia al breakeven solo se informa. **Los valores iniciales (1×, 10 días, 100 %) no salen del análisis: hay que calibrarlos con los CDS del logbook.**

Reutiliza: comisiones, filtros de calidad de la empresa (quien llama los aplica antes), cierres diarios para la SMA200. No reutiliza: delta/yield/crédito de la put vendida ni IV Rank (invertido en un débito).

**Conectado (2026-11-02), apagado por defecto (`scanner.cds.enabled: false`):**
- `ContractSyncer.sync` añade las calls (`call_candidate_contracts`) al catálogo; `HybridGateway.qualify_contracts` las valida con la lista de calls del proveedor (`get_call_listing`).
- `RefreshJob._cds_in_scope` las cotiza (DTE y OTM del CDS); `_in_scope` solo acepta puts y las calls nunca piden margen ni tamaño de TWS. Los escaneos de puts (`AppService._scan_snapshots`, simulador) ignoran las calls; `_scan_call_snapshots` las da al CDS.
- `AppService.scan_cds` (calidad de la empresa = filtros activos del criterio por defecto del Scanner; comisiones y precio de venta del Scanner) y el instrumento `cds` del Scanner (`/scanner?mode=cds`, `ui/routes_cds.py`; la pestaña CDS y `/cds.html` se retiraron el 2026-11-03: `/cds` redirige al Scanner).
- **Coste medido (2026-11-02, watchlist de 2.351 tickers, mercado cerrado, activado con `enabled: true`):** actualización diaria 212,6 s; 37.533 calls guardadas de 1.921 tickers (unas 20 por ticker; 33.578 con bid), +9 MB en la base (225 MB); cotizar los 38.474 contratos nuevos 85,6 s; la pestaña `/cds` carga en 1,5 s y sale con 946 spreads con los valores por defecto (sin revisar uno a uno). Sin medir: el ciclo periódico con el mercado abierto. Para generar las calls en una instalación existente hay que hacer una actualización diaria completa (los tickers ya actualizados hoy no se vuelven a sincronizar).

## Perfil «MTO CDS» dentro del Scanner (2026-11-03)

El CDS es un **tercer instrumento de los perfiles del Scanner** (`scanner/profiles.py::INSTRUMENTS = put | spread | cds`; `Profile.cds` guarda los parámetros de la URL del Scanner con `mode=cds`). El perfil **«MTO CDS»** se siembra una vez (`AppService.seed_mto_cds_profile`, `meta.mig_mto_cds_profile`; si lo borras no vuelve) con los valores de `scanner.cds`, aparece en la barra de perfiles del Scanner y en `/scanner/config` (nombre, color, orden, duplicado, borrado) y su botón abre **`/scanner?mode=cds&profile=…`** (misma pantalla y misma plantilla `scanner.html` que los demás perfiles; `/cds` es solo una redirección). El formulario es editable (`ui/cds_form.py`, `scanner/cds_profile.py`) y «Guardar en perfil» reutiliza `POST /scanner/profile/save` (guarda el juego `cds` del perfil).

**Integrado en el Scanner (2026-11-03).** Mismo diseño que put y spread: perfiles arriba a la izquierda, secciones plegables de filtros («Ventana: DTE y % OTM», «Ancho del spread», «Débito», «Liquidez de las opciones» (la del ticker más el OI, el spread y los tamaños de cada pata), «Rentabilidad mínima esperada», «Tendencia: SMA200», «Precio de venta de la vendida»; el `−`/`+` de DTE, % OTM, objetivo, días y X escanea), y en la columna de resultados el recuento, las líneas **Base** (ventana, anchos, movimiento necesario, precio de venta), **Activados** (débito, OI, spread de las patas, mejor ancho, ancho/strike, retorno máx., SMA200) y **Desactivados**, con chips que abren su sección y se marcan si se apartan del perfil guardado. El panel **«¿Cuánto descarta cada filtro?»** (`scanner/cds_impact.py`, `AppService.scan_cds_impact`, `GET /scanner/impact?mode=cds`) construye los CDS UNA vez con todos los filtros opcionales relajados y evalúa cada filtro como predicado (sin repetir el escaneo): cuenta todos los anchos (sin «mejor ancho»), y su total coincide con un escaneo real con `best_width` apagado (`tests/unit/test_cds_impact.py`). La tabla (`_contract_cols.html::cds_head/cds_cells`, filas `routes_cds.CdsRow`) usa las columnas de contexto de las puts (ticker con tooltip y avisos, sector, precio, miniatura con los dos strikes, deltas, IV, IV Rank/Percentile con barras, spread y OI de cada pata, resultados, EPS) y las propias: comprada/vendida, OTM %, ancho, débito, pérdida %NLV, retorno máx. (con barra), breakeven, delta neta, necesario %, esperado %, **× esperado** (columna destacada y orden inicial) y vs SMA200; sin «Téc.» (son señales de la put vendida) ni selección para simular (`/simulate` solo conoce puts). El gráfico ampliado y la miniatura (`/chart/strike` y `/chart/mini.svg` con `kind=cds`, `charts.py` con `call=True`) dibujan la comprada en azul, la vendida en rojo y el débito pagado, sin el «último toque» (es de la put vendida). Se muestran los 300 con menor movimiento necesario. Con `scanner.cds.enabled: false` el perfil muestra el aviso y los filtros, sin tabla. Medido con la copia de la base (35.432 calls, 13.502 spreads con los valores por defecto): la página carga en 0,6 s y el panel de impacto en 3,5 s.

**Salida en el objetivo (2026-11-03).** El objetivo (`target_gain_pct`, «Objetivo % del débito» del formulario, 20 % por defecto) es configurable. Al alcanzarlo el spread vale `débito × (1 + objetivo)`: la tabla muestra **Cobro salida** (ese valor × multiplicador, menos la comisión de cierre), **Ganancia salida** (cobro − débito) y **Retorno salida %** (ganancia / débito = objetivo menos comisión de cierre). La **puntuación** pasó a `prob × retorno salida / 100` (antes usaba el retorno máximo al vencimiento): penaliza los débitos pequeños, donde las comisiones se comen el objetivo. Se descartan los spreads cuyo valor de salida supera el ancho. La comisión de cierre se estima igual que la de apertura (`cost_ps`). Filtro opcional `min_exit_profit_usd` (casilla «Ganancia salida ≥ $», apagado por defecto): descarta los spreads que ganarían menos de N $ netos por contrato al cerrar en el objetivo. El retorno máximo al vencimiento sigue como columna y filtro de apoyo.

**Reducir las salidas: probabilidad y puntuación (2026-11-03).** Antes la tabla salía ordenada por el múltiplo «necesario/esperado» y con miles de filas. Ahora cada CDS lleva `prob_pct` = probabilidad de que el subyacente TOQUE el nivel que da el objetivo en `move_days` días, `2·(1 − Φ(z))` con `z` = necesario/esperado (principio de reflexión, sin deriva; es monótona en `z`) y `score` = `prob_pct × retorno de salida / 100` (ver arriba). La tabla se ordena por **Puntuación** (descendente). Dos reductores nuevos, editables en «Rentabilidad mínima esperada» y guardados en el perfil: `min_prob_pct` (40 %, casilla `use_prob`; equivale a `z` ≤ 0,84, más estricto que el antiguo `max_required_move_mult` = 1, que dejaba pasar hasta el 32 %) y `top_per_ticker` (2; 0 = todos), que se queda con los de mayor puntuación de cada ticker. `best_width` elige ahora el ancho de mayor puntuación. Limitaciones: la probabilidad es un modelo lognormal sin deriva, con la IV a `move_days` días (`iv_at_horizon`) como volatilidad; **40 % y 2 por ticker no están calibrados con el logbook**. Un perfil guardado antes de este cambio no trae `use_prob`: al cargarlo no filtra por probabilidad (sí aplica el tope por defecto, 2).

**Filtros del ticker en el perfil CDS (2026-11-03).** Antes el escaneo CDS decía aplicar «los filtros de empresa del criterio por defecto», pero ese criterio no activa ninguno (`criteria_from_settings`): no se descartaba ningún ticker por calidad. Ahora el perfil guarda los suyos, con las secciones «Calidad de la empresa» (beneficios 12 m > 0, trimestres con beneficios, FCF > 0, pasivo/patrimonio máx.), «Liquidez de las opciones» (tastytrade 1–5), «Precio del subyacente» y «Fuentes del Universo», leídos con el mismo parser que el Scanner de puts (`ui/cds_form.py::_read_ticker_filters`, `read_quality`). Se aplican antes de buscar spreads (`AppService._cds_ticker_filters`, lista de `TickerFilter`) y en el panel de impacto cuentan como un filtro más (claves `cds_profit`, `cds_quarters`, `cds_fcf`, `cds_lev`, `cds_liq`, `cds_price`, `cds_sources`; «Quitar» los apaga). Empiezan apagados: un perfil sin ellos escanea todos los tickers con calls guardadas. No hay «evitar resultados» (con DTE 200–500 siempre hay unos resultados antes del vencimiento y descartaría casi todo) ni los grados de solvencia del Universo. Los cierres del CDS tienen memoria propia (`cds_closes`), independiente del escaneo de puts.

**Límites de la integración:** los filtros propios de la put vendida (delta, técnicos, medias, soporte, IV Rank, calidad mínima de la put, resultados) no aparecen en el perfil CDS ni se aplican. «Todos los perfiles» (`/scanner/todos`) SÍ incluye los perfiles CDS desde 2026-11-12: los CDS que cumplen los filtros de algún perfil CDS salen en una segunda tabla (otras columnas que las puts), ordenados por puntuación y con la columna «Estrategias»; el recuento por perfil aparece junto a su casilla. El reconocimiento de puts por DTE (`classify.dte_range`) y la comparación de Comunidad/Radar ignoran los perfiles CDS; `/exits` los trata con las reglas de debit spread.

**La ventana es la del catálogo.** DTE (200–500), % OTM de la comprada (25–130) y los anchos que se pueden elegir son los de `scanner.cds` (config.yaml), como `scanner.candidates` para las puts: el formulario solo puede estrecharlos (un valor fuera se recorta y se avisa; un ancho que el catálogo no guarda se descarta). Débito, OI, spread de las patas, movimiento esperado, retorno máximo y SMA200 no afectan al catálogo y son libres.

**Valores revisados con el logbook (51 CDS reconstruidos del CSV, 2026-11-03):**
- **Débito máximo 2.150 $** (máximo del logbook: NVDA 250/350 dic. 2028, 2.145 $). Solo 5 de los 51 superan 650 $ (META 920/1000 1.040 $, NVDA ×2 2.140–2.145 $, RDDT 200/300 1.670 $, UBER 100/150 650 $); 9 están por debajo de 150 $ (mínimo 57 $, no se ha cambiado el mínimo).
- **Anchos:** 2–100 $ con mediana 50 $; los más frecuentes son 50 (13), 60 (7), 20 (4) y 100 (4). Son **proporcionales al precio**: strike < 50 $ → 3–25 $ (mediana 8), 50–100 $ → 15–30 $, 100–200 $ → 20–85 $, 200–400 $ → 50–100 $. Los anchos ≤ 10 $ solo aparecen en acciones baratas (RIVN, TSLL, ONDS, TTD, EOSE, GIS), así que no sobran: son los de esos tickers. Lista nueva: 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100 (antes 5/10/20/50, que solo cubrían 19 de los 51). **Coste del catálogo: +4 % de calls** (los strikes vendidos caen en la rejilla de los comprados), medido sobre las cadenas guardadas.
- **Ancho / strike de la comprada:** p10 8,7 %, mediana 22 %, p90 50 % (máximo 260 %: una acción de 8 $). `width_pct_min: 7` y `width_pct_max: 110` descartan anchos ridículos en acciones caras (p. ej. 5 $ sobre META) y enormes en las baratas.
- **SMA200:** el filtro (`sma200_below_pct_min`) queda **apagado por defecto** (era 10 %); la columna «vs SMA200 %» se calcula y se muestra siempre que haya 200 cierres (en verde si el precio está al menos un 10 % por debajo).
- **Técnicos de entrada y medias móviles / EMA (2026-11-06):** el CDS tiene las mismas secciones «Técnicos de entrada» (señales: cercanía a la MA50 o al soporte, RSI que rebota, Bollinger %B y ancho de bandas, con «al menos N de las marcadas») y «Medias móviles / EMA» (precio frente a MA50/100/200 y EMA9/20, comparaciones entre medias y pendiente, velas diarias/semanales/mensuales) que el Scanner de puts, con los mismos nombres de parámetros de la URL. Empiezan **apagados** (los valores de las cajas son los de `scanner.strategy`). Son filtros del ticker, como los de calidad: `ui/cds_form.py::_read_technical` los lee y `AppService._cds_technical_filters` los aplica con `scanner/technical.py::TechnicalFilter` y `scanner/signals.py` sobre los cierres diarios (un ticker sin histórico suficiente queda fuera), y cada grupo tiene su fila en el panel de impacto (`cds_entry`, `cds_ma`). No hay tendencia ni soporte/último toque: dependen del strike de una put. Se guardan en el perfil con el resto de claves.
- **Mejor ancho:** `best_width` deja una fila por ticker, vencimiento y call comprada (la del menor movimiento necesario).
- **Limitación del emparejado:** el CSV no identifica los combos; las patas se emparejan por cuenta, ticker, vencimiento y minuto de apertura. 5 grupos tienen dos patas largas o cortas (entradas por duplicado de 1 contrato); los 4 débitos > 1.000 son CDS únicos.

**Efecto sobre los resultados (copia de la base, 2026-11-03):** con los valores nuevos salen 21.328 spreads de 284 tickers (antes 2.200 de 76); 6.080 con «mejor ancho»; con la SMA200 a −10 % bajarían a 9.554. La página muestra los 300 de menor movimiento necesario. META 930/1000 de 2028-01-21 (912 $, ×0,90 el movimiento esperado, −3,1 % bajo la SMA200) ya aparece.

**Consultar un CDS concreto (2026-11-08; `scanner/cds_check.py::check_cds`, `AppService.check_cds`, `GET /scanner/cds-check`, plantilla `_cds_check.html`).** Panel plegable «Consultar un CDS» sobre la tabla del Scanner CDS: ticker, vencimiento (fecha) y strikes de la comprada y la vendida. Muestra los parámetros que tendría el spread en la tabla (débito, retorno, delta neta, necesario/esperado, probabilidad, salida, puntuación, SMA200, patas, OI, tamaños…) y la lista de filtros activos con **✓/✗, lo que exige el formulario y lo que tiene el spread**; los parámetros que incumplen un filtro salen en rojo. Se evalúa con los filtros tal como están aplicados (los de la URL, igual que el panel de impacto). El spread se construye con todos los filtros y la ventana relajados (`cds_impact.relaxed`), así que existe aunque el Scanner lo descarte; cada filtro se evalúa con los mismos predicados que el panel de impacto (`cds_impact.predicates`) más DTE, % OTM, anchos elegidos, filtros del ticker (calidad, técnicos, precio, fuentes) y modo CDS del ticker. «Aparece en el escaneo» sale del escaneo real, de modo que avisa si «mejor ancho» o «máx. por ticker» lo dejan fuera aunque cumpla todo. Si no se puede calcular (pata no guardada, sin mercado válido, sin delta, delta neta ≤ 0, débito ≥ ancho…) dice por qué y lista los vencimientos y strikes guardados. Solo consulta las calls guardadas: un contrato fuera de la ventana del catálogo no existe en la base.

## Pendiente

- Medir el coste del ciclo con `cds.enabled` y ajustar el rango si hace falta.
- Presets «MTO CDS 200–400» (más perfiles CDS: se crean desde el Scanner con «Guardar en perfil»), filtros de calidad propios del CDS (hoy los del criterio por defecto) y calibrar el filtro de movimiento necesario con el logbook.
- Añadir los CDS abiertos al Panel con el perfil MTO (las salidas ya existen en `other_mgmt`).

- Código: strikes vendidos por encima del precio en el Scanner (descuento ≤ 0 %) y presets «MTO PCS ITM» y «MTO CDS OTM».
- Registrar la pata de acciones tras una asignación para medir la rueda completa con exactitud (hoy solo se estima a partir de las calls cubiertas).
- Sustituir el CSV del logbook por `list_trades`/`get_portfolio` de la API de Tortuga y comprobar si trae las acciones asignadas.
- Repetir el análisis cuando los CDS abiertos venzan, para sustituir las marcas del modelo por resultados reales.

**Filtro «Sin resultados en la ventana de días» (2026-11-03; `scanner.cds.exclude_earnings`, casilla `use_earn`, activo por defecto).** Descarta el ticker si su próxima presentación de resultados (`TickerInfo.next_earnings`) cae entre hoy y dentro de `move_days` días (inclusive): el salto por resultados no está en la IV a ese plazo y falsea el movimiento esperado. Una fecha ya pasada (es la última, no la próxima) o desconocida no excluye. Aparece en el panel de impacto como «Sin resultados en la ventana». Distinto del filtro de resultados del Scanner de puts, que no se ofrece aquí porque con DTE 200–500 siempre hay resultados antes del vencimiento.

**Precio de salida en el gráfico del CDS (2026-11-03).** El gráfico grande del strike del perfil MTO CDS marca con una línea verde punteada el precio del subyacente al que se alcanza el objetivo (precio actual × (1 + Necesario %), el de la columna «Precio salida»), con su etiqueta «Salida $X · necesario +N %», una franja de resumen y un lugar en la leyenda. Llega por los parámetros `exit` y `need` de `GET /chart/strike` (`data-exit`/`data-need` del enlace de la fila). La miniatura no lo lleva.

**Liquidez por pata y tamaños (2026-11-05).** Los filtros de las patas ya no son uno para las dos: `min_long_oi` / `min_short_oi` (OI de la comprada y de la vendida, 10 cada uno), `max_long_spread_pct` / `max_short_spread_pct` (spread bid/ask de cada una, 25 %; sustituyen a `max_leg_spread_pct`) y, opcionales y apagados por defecto, `min_bid_size` y `min_ask_size` (tamaño mínimo del bid y del ask, que se exige a LAS DOS patas; una pata sin dato de tamaño no pasa). En el formulario, dentro de la sección «Liquidez de las opciones»: «OI mín. comprada/vendida», «Spread máx. comprada/vendida (%)» y las casillas «Bid size ≥» y «Ask size ≥» (claves `oi`, `oi_s`, `ls`, `ls_s`, `use_bsz`/`bsz`, `use_asz`/`asz`; un perfil guardado antes, con solo `oi` y `ls`, aplica ese valor a las dos patas). El panel «¿Cuánto descarta cada filtro?» los separa: `cds_oi`, `cds_oi_s`, `cds_spread`, `cds_spread_s`, `cds_bsz`, `cds_asz`. La tabla añade las columnas **Bid size comp.**, **Ask size comp.**, **Bid size vend.** y **Ask size vend.** junto a las de OI y spread de cada pata. El tamaño del ask es nuevo: `OptionQuote.ask_size` / `ContractSnapshot.ask_size` (tastytrade `Quote.ask_size`; IBKR `askSize`), columna `snapshots.ask_size` (migración v41). Son los tamaños del proveedor de datos: con tastytrade son 5–10 veces menores que los de TWS (ver «Bid size de TWS» en CLAUDE.md), y a las calls del CDS no se les pide el tamaño a TWS, así que conviene un mínimo bajo.

**Todos los anchos (2026-11-06; `scanner.cds.all_widths`, casilla `allw` en «Ancho del spread»).** Con ella la vendida puede ser CUALQUIER strike guardado del mismo vencimiento por encima de la comprada, no solo los `width_options` (la lista se ignora; siguen mandando `width_pct_min/max` y el resto de filtros). Marcada por defecto (2026-11-06; un perfil o enlace anterior a la casilla, sin `allw_set`, se lee con ella marcada). Medido con la copia de la base (perfil MTO CDS): 448 → 546 filas (+22 %), los mismos 44 tickers, 9 → 53 anchos distintos, puntuación media 10,3 → 10,7 (top 100: 12,1 → 12,7), sin ninguna mejora de más de 1 punto en el mejor spread de un ticker; el escaneo pasa de 0,4 a 0,7 s. Solo hay vendidas que el catálogo ya guarda (las calls de la ventana de la comprada más las `width_options` por encima de cada una), así que no amplía lo que se cotiza. Ejemplo: NOW 175/220 (puntuación 9,5) pasa a salir; 175/260 no, por el spread de la vendida.

**Catálogo con todos los strikes de la vendida (2026-11-06; `scanner.cds.all_short_strikes`, activo por defecto).** Además de las calls `width_options` $ por encima de cada comprada, `call_candidate_contracts` guarda CUALQUIER strike de la cadena por encima de ella hasta `width_pct_max` % de su strike (sin tope si es `null`), para que «Todos los anchos» tenga cobertura completa también por encima del 130 % OTM (antes un ancho fuera de la lista cuya vendida cayera ahí no existía en la base). Medido con las cadenas guardadas (1.163 tickers): ≈ +5,5 % de calls (≈ 27.500 → ≈ 29.000, estimación; hoy hay 25.164 con DTE ≥ 200). No se midió el tiempo extra por ciclo de cotización. `all_short_strikes: false` recupera el catálogo anterior. La primera actualización diaria tras el cambio las valida y guarda.

**Columna «Realizado %» (2026-11-09; `CdsResult.realized_move_pct`, `scanner/cds.py::realized_vol`).** Solo informativa, no filtra: movimiento típico a `move_days` días con la volatilidad realizada de las últimas 60 sesiones (desviación de los log-retornos × √252 × √(días/365)), junto a «Esperado %» (IV). Sirve para ver en qué valores la IV está inflada o corta frente a lo que la acción se mueve de verdad; vacía sin 61 cierres. Se probó y descartó un filtro del breakeven al vencimiento frente a esa volatilidad: la estrategia sale en ~10 días o con resultado positivo y no espera al vencimiento.

## Put credit spreads con DTE > 100 días (análisis 2026-11-12, `estrategia/logbook_mto_2026-11-12.csv`)

Logbook de la cuenta 50K (281 patas). Se emparejaron las patas put por ticker, hora de apertura y vencimiento (cuidado: varias filas del mismo spread salen duplicadas, p. ej. T, PINS, ARCC; hay que dividir por la cantidad total).

**Qué hay realmente:** de ~18 put spreads verticales solo UNO tiene DTE > 100 y es puro: **HIMS 47/50, vencimiento 2027-12-17, 3 contratos, abierto el 2026-11-10 (401 DTE), crédito 2,82 $ sobre ancho 3 $ → riesgo 0,18 $, ratio beneficio/riesgo 15,7.** El resto de los PCS tiene 30–98 DTE y ratio 1,2–2,1 (los «999» de un primer cálculo eran duplicados de filas). UBER (391 DTE, put 35/60 + call 100) es un combo, no un PCS; SOXL 30/15 (275 DTE, 1×2) es un ratio de puts con débito. **No hay ningún PCS largo cerrado: no se puede medir acierto ni P&L; la muestra es 1 operación de hace 2 días.** Lo que sigue es análisis de estructura, no de resultados.

### El ratio 15,7 es profundidad ITM, no ventaja

- HIMS cotiza a 20,81 $ y el strike vendido es 50 $: el spread está **+140 % ITM**. Vender 50 / comprar 47 por 2,82 $ equivale, por paridad put-call, a un **Call Debit Spread 47/50** (comprada 126 % OTM, 401 DTE, ancho 3 $) más efectivo: exactamente la ventana del CDS (DTE 200–500, comprada 25–130 % OTM). El PCS «muy ITM» **es** un CDS muy OTM construido con puts.
- Se gana solo si el subyacente sube: pérdida máxima con HIMS ≤ 47, beneficio máximo con HIMS ≥ 50; breakeven ≈ 47,18 (+127 %). Con IV 75 % la probabilidad lognormal de acabar > 47,18 es ≈ 8 % (> 50: ≈ 7 %) frente a un punto de equilibrio de 0,18/3 = 6 %: **el ratio alto solo compensa una probabilidad baja; el margen sobre el equilibrio es pequeño y depende de la IV**.
- De los 0,18 $ de «riesgo», la mayor parte no es prima de la opción sino el valor del dinero: con tipos del 4 % a 1,1 años el ancho descontado es ≈ 2,87 $; el valor teórico del CDS equivalente es ≈ 0,05–0,26 $ según las patas (calls de HIMS: 47 → 2,26/2,63; 50 → 1,82/2,54; el comprador paga hasta 0,81 $ al natural). El crédito puede ser un efecto del mid y de la horquilla ancha de las puts ITM (70 P: 48,95/51,10; 75 P: 52,50/56,50), no una ventaja real: **medir siempre el ratio con el precio al natural (vendida al bid, comprada al ask)**.
- En los PCS cortos del propio logbook (ya analizados arriba) el beneficio llega por rebote rápido, no por vencer; el DTE largo da tiempo, pero solo sirve con una salida en el primer rebote (+20 %, ver CDS).

### Puts frente a calls para la misma estructura

| | PCS ITM largo (puts) | CDS (calls) |
|---|---|---|
| Payoff | idéntico (paridad) | idéntico |
| Riesgo de asignación anticipada | sí: la put vendida muy ITM con poco valor extrínseco (deep ITM, tipos 4 %) | no (HIMS no paga dividendo; las calls vendidas OTM casi no se ejercen) |
| Liquidez | puts ITM con horquillas de 2–4 $ por pata | calls OTM con horquillas menores y más OI |
| Margen | what-if de TWS (Reg T: ancho − crédito) | débito pagado, sin margen |
| Catálogo en la app | NO guardado: puts solo −5…−35 % bajo el precio, DTE 1–35 | sí: `scanner.cds` (DTE 200–500, OTM 25–130 %) |
| Efectivo | cobra el crédito (interés del efectivo) | paga el débito |

### Propuesta para implementar «algo parecido al CDS»

1. **No duplicar el motor.** `scanner/cds.py` ya construye el payoff equivalente; un escáner de PCS ITM largo por puts exigiría ampliar el catálogo de puts a strikes por encima del precio con DTE 200–500 (puts ≈ el doble de contratos que el CDS, con horquillas peores) para obtener lo mismo.
2. **Mejora barata en el CDS:** columna/valor «crédito PCS equivalente» = ancho·descuento − débito (con el tipo libre de riesgo) y marca «ejecutable como PCS» cuando el crédito real de las puts (bid/ask) supere ese valor teórico; solo informativo, a partir de las puts que haya en la cadena de tastytrade (consulta puntual con la cadena, sin guardarlas).
3. **Si se quiere el PCS real:** perfil de escaneo «MTO PCS largo» con strike vendido ≥ 50 % sobre el precio, DTE 200–500, ancho 3–25 $, ratio medido al natural, probabilidad de beneficio máximo (delta de la vendida ≈ 1 − P) ≥ punto de equilibrio + margen, extrínseco de la put vendida ≥ intereses del strike a DTE (anti-asignación) y modo `cds` del ticker. Misma salida que el CDS (objetivo +20 %, revisar a ≤ 90 DTE; `long_review_dte`).
4. **Antes de implementar:** esperar a tener cerrados varios (el logbook solo trae HIMS) y revisar HIMS con el precio real de las puts (la app no las guarda).

### Filtro anti-asignación (put vendida muy ITM)

El titular de una put americana muy ITM la ejerce cuando su valor extrínseco es menor que el interés que ganaría cobrando el strike hoy.

```
extrínseco = precio_put − (K_vendida − S)     # usar el BID (lo que cobras al cerrar)
carry      = K_vendida × r × DTE/365          # interés sobre el strike hasta el vencimiento
colchón    = extrínseco / carry
pasa si colchón ≥ 1,25   y   |delta vendida| < 0,95
```

- Los dividendos no hacen falta: bajan el precio de la acción y hacen menos atractivo ejercer una put, así que el filtro es conservador. `r` va en config (tipo del efectivo de IBKR o fijo, 4 %).
- |delta| ≈ 1 indica que la put cotiza a intrínseco y el extrínseco no es fiable.
- **HIMS 47/50 (aproximado, precios del logbook y cierre del 11-11):** extrínseco de la put 50 ≈ 31,80 − 29,19 = 2,61 $; carry a 401 DTE con r = 4 % ≈ 2,20 $; colchón ≈ 1,19. Pasa con umbral 1,0 y falla con 1,25: está al límite (con el bid sería menor).
- **Por qué importa con riesgo acotado:** una asignación de 3 contratos carga 300 acciones a 50 $ (≈ 15.000 $) de golpe, un problema de margen/efectivo en una cuenta de 50K, y obliga a ejercer o vender antes de tiempo la put comprada 47 perdiendo su extrínseco.
- **Encaje en la app (propuesto):** filtro del perfil «MTO PCS largo» con casilla propia y fila en «¿Cuánto descarta cada filtro?» (mismo patrón que `cds_impact.predicates`); config `min_extrinsic_carry_ratio` (1,25), `risk_free_rate_pct`, `max_short_abs_delta` (0,95). Además, una alerta en el Panel si el colchón de una put vendida ya abierta baja de 1 (el extrínseco se erosiona al moverse la acción); es lo más útil y no depende del escáner.
- El CDS no lo necesita: la call vendida OTM casi nunca se ejerce.

*Es análisis de una operación abierta y de estructura, no recomendación de inversión.*

## Delta neta mínima del CDS (2026-11-12)

Para salir en días o semanas, cuando sube la acción, el spread tiene que ganar valor con una subida pequeña: lo mide la delta neta (delta de la comprada − delta de la vendida), sobre todo en relación al débito. El filtro «Movimiento necesario ≤ esperado» ya lo contiene de forma indirecta (necesario = objetivo × débito / Δ neta), pero dejaba pasar spreads con Δ neta de 0,05 y débito de céntimos.

- `scanner.cds.min_net_delta` (`CdsSettings`, **0,15** por defecto; `None` = sin filtro): casilla «Δ neta ≥» en la sección «Rentabilidad mínima esperada» del Scanner CDS (`use_nd`/`nd`), chip, fila «Delta neta» en «¿Cuánto descarta cada filtro?» (`cds_ndelta`) y en la consulta de un CDS.
- Columna informativa «Δ neta / débito» (`CdsResult.delta_per_debit`: lo que gana el spread por cada $1 que sube la acción respecto a lo que cuesta; más alto = mejor para una salida rápida).
- Los perfiles CDS guardados antes de este cambio no traen la casilla `use_nd`, así que se leen sin el filtro hasta que se marque y se vuelva a guardar el perfil. Orientación (sin calibrar con el logbook): 0,20–0,35.

### Realizado / esperado e IV Rank máximo (2026-11-12)

Dos filtros opcionales más en «Rentabilidad mínima esperada», **apagados por defecto** (`None`), con su casilla, chip, fila en «¿Cuánto descarta cada filtro?» y en la consulta de un CDS:

- `scanner.cds.min_realized_ratio` (caja con 0,6): cociente movimiento realizado (60 sesiones) / esperado (IV). Por debajo de ~0,6 la IV está inflada y la probabilidad calculada (que usa el esperado) es optimista. Nueva columna «Real./Esp.» (en rojo por debajo de 0,6). Sin historial suficiente no descarta.
- `scanner.cds.max_iv_rank` (caja con 60): IV Rank máximo del subyacente (el de la call comprada). Con IV en máximos las opciones son caras y una caída de la IV recorta la ganancia de la delta. Sin dato no descarta.

### Probabilidad ajustada en la puntuación (2026-11-12)

La puntuación (orden de la tabla) usa ahora la **probabilidad ajustada** (`CdsResult.prob_adj_pct`, columna «Prob. ajust. %»): la misma fórmula de tocar el nivel, pero con el movimiento `(1 − w) × esperado + w × realizado` (`scanner.cds.realized_weight`, **0,5** = la media; 0 recupera la puntuación anterior; 1 = solo el realizado). Sin historial (sin realizado) vale la original. Los filtros «Prob. objetivo ≥» y «Necesario ≤ esperado» siguen sobre la probabilidad y el múltiplo con el esperado (IV). La Δ neta / débito no se suma aparte: ya está dentro del movimiento necesario. Sin calibrar con el logbook.
