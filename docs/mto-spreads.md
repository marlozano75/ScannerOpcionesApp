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

**Rentabilidad mínima esperada (filtro obligatorio):** movimiento necesario = `target_gain_pct` (20 %) × débito / delta neta (delta comprada − delta vendida), en % del precio; debe ser ≤ `max_required_move_mult` (1) × el movimiento esperado a `move_days` (10) días = IV30 × √(días/365) (sin IV30, la IV de la pata). Sin delta o sin IV se descarta. De apoyo: retorno máximo `(ancho − débito)/débito` ≥ `min_max_return_pct` (100 %). La distancia al breakeven solo se informa. **Los valores iniciales (1×, 10 días, 100 %) no salen del análisis: hay que calibrarlos con los CDS del logbook.**

Reutiliza: comisiones, filtros de calidad de la empresa (quien llama los aplica antes), cierres diarios para la SMA200. No reutiliza: delta/yield/crédito de la put vendida ni IV Rank (invertido en un débito).

**Conectado (2026-11-02), apagado por defecto (`scanner.cds.enabled: false`):**
- `ContractSyncer.sync` añade las calls (`call_candidate_contracts`) al catálogo; `HybridGateway.qualify_contracts` las valida con la lista de calls del proveedor (`get_call_listing`).
- `RefreshJob._cds_in_scope` las cotiza (DTE y OTM del CDS); `_in_scope` solo acepta puts y las calls nunca piden margen ni tamaño de TWS. Los escaneos de puts (`AppService._scan_snapshots`, simulador) ignoran las calls; `_scan_call_snapshots` las da al CDS.
- `AppService.scan_cds` (calidad de la empresa = filtros activos del criterio por defecto del Scanner; comisiones y precio de venta del Scanner) y el instrumento `cds` del Scanner (`/scanner?mode=cds`, `ui/routes_cds.py`; la pestaña CDS y `/cds.html` se retiraron el 2026-11-03: `/cds` redirige al Scanner).
- **Coste medido (2026-11-02, watchlist de 2.351 tickers, mercado cerrado, activado con `enabled: true`):** actualización diaria 212,6 s; 37.533 calls guardadas de 1.921 tickers (unas 20 por ticker; 33.578 con bid), +9 MB en la base (225 MB); cotizar los 38.474 contratos nuevos 85,6 s; la pestaña `/cds` carga en 1,5 s y sale con 946 spreads con los valores por defecto (sin revisar uno a uno). Sin medir: el ciclo periódico con el mercado abierto. Para generar las calls en una instalación existente hay que hacer una actualización diaria completa (los tickers ya actualizados hoy no se vuelven a sincronizar).

## Perfil «MTO CDS» dentro del Scanner (2026-11-03)

El CDS es un **tercer instrumento de los perfiles del Scanner** (`scanner/profiles.py::INSTRUMENTS = put | spread | cds`; `Profile.cds` guarda los parámetros de la URL del Scanner con `mode=cds`). El perfil **«MTO CDS»** se siembra una vez (`AppService.seed_mto_cds_profile`, `meta.mig_mto_cds_profile`; si lo borras no vuelve) con los valores de `scanner.cds`, aparece en la barra de perfiles del Scanner y en `/scanner/config` (nombre, color, orden, duplicado, borrado) y su botón abre **`/scanner?mode=cds&profile=…`** (misma pantalla y misma plantilla `scanner.html` que los demás perfiles; `/cds` es solo una redirección). El formulario es editable (`ui/cds_form.py`, `scanner/cds_profile.py`) y «Guardar en perfil» reutiliza `POST /scanner/profile/save` (guarda el juego `cds` del perfil).

**Integrado en el Scanner (2026-11-03).** Mismo diseño que put y spread: perfiles arriba a la izquierda, secciones plegables de filtros («Ventana: DTE y % OTM», «Ancho del spread», «Débito y patas», «Rentabilidad mínima esperada», «Tendencia: SMA200», «Precio de venta de la vendida»; el `−`/`+` de DTE, % OTM, objetivo, días y X escanea), y en la columna de resultados el recuento, las líneas **Base** (ventana, anchos, movimiento necesario, precio de venta), **Activados** (débito, OI, spread de las patas, mejor ancho, ancho/strike, retorno máx., SMA200) y **Desactivados**, con chips que abren su sección y se marcan si se apartan del perfil guardado. El panel **«¿Cuánto descarta cada filtro?»** (`scanner/cds_impact.py`, `AppService.scan_cds_impact`, `GET /scanner/impact?mode=cds`) construye los CDS UNA vez con todos los filtros opcionales relajados y evalúa cada filtro como predicado (sin repetir el escaneo): cuenta todos los anchos (sin «mejor ancho»), y su total coincide con un escaneo real con `best_width` apagado (`tests/unit/test_cds_impact.py`). La tabla (`_contract_cols.html::cds_head/cds_cells`, filas `routes_cds.CdsRow`) usa las columnas de contexto de las puts (ticker con tooltip y avisos, sector, precio, miniatura con los dos strikes, deltas, IV, IV Rank/Percentile con barras, spread y OI de cada pata, resultados, EPS) y las propias: comprada/vendida, OTM %, ancho, débito, pérdida %NLV, retorno máx. (con barra), breakeven, delta neta, necesario %, esperado %, **× esperado** (columna destacada y orden inicial) y vs SMA200; sin «Téc.» (son señales de la put vendida) ni selección para simular (`/simulate` solo conoce puts). El gráfico ampliado y la miniatura (`/chart/strike` y `/chart/mini.svg` con `kind=cds`, `charts.py` con `call=True`) dibujan la comprada en azul, la vendida en rojo y el débito pagado, sin el «último toque» (es de la put vendida). Se muestran los 300 con menor movimiento necesario. Con `scanner.cds.enabled: false` el perfil muestra el aviso y los filtros, sin tabla. Medido con la copia de la base (35.432 calls, 13.502 spreads con los valores por defecto): la página carga en 0,6 s y el panel de impacto en 3,5 s.

**Salida en el objetivo (2026-11-03).** El objetivo (`target_gain_pct`, «Objetivo % del débito» del formulario, 20 % por defecto) es configurable. Al alcanzarlo el spread vale `débito × (1 + objetivo)`: la tabla muestra **Cobro salida** (ese valor × multiplicador, menos la comisión de cierre), **Ganancia salida** (cobro − débito) y **Retorno salida %** (ganancia / débito = objetivo menos comisión de cierre). La **puntuación** pasó a `prob × retorno salida / 100` (antes usaba el retorno máximo al vencimiento): penaliza los débitos pequeños, donde las comisiones se comen el objetivo. Se descartan los spreads cuyo valor de salida supera el ancho. La comisión de cierre se estima igual que la de apertura (`cost_ps`). El retorno máximo al vencimiento sigue como columna y filtro de apoyo.

**Reducir las salidas: probabilidad y puntuación (2026-11-03).** Antes la tabla salía ordenada por el múltiplo «necesario/esperado» y con miles de filas. Ahora cada CDS lleva `prob_pct` = probabilidad de que el subyacente TOQUE el nivel que da el objetivo en `move_days` días, `2·(1 − Φ(z))` con `z` = necesario/esperado (principio de reflexión, sin deriva; es monótona en `z`) y `score` = `prob_pct × retorno de salida / 100` (ver arriba). La tabla se ordena por **Puntuación** (descendente). Dos reductores nuevos, editables en «Rentabilidad mínima esperada» y guardados en el perfil: `min_prob_pct` (40 %, casilla `use_prob`; equivale a `z` ≤ 0,84, más estricto que el antiguo `max_required_move_mult` = 1, que dejaba pasar hasta el 32 %) y `top_per_ticker` (2; 0 = todos), que se queda con los de mayor puntuación de cada ticker. `best_width` elige ahora el ancho de mayor puntuación. Limitaciones: la probabilidad es un modelo lognormal sin deriva, con la IV30 (o la de la pata) como volatilidad; **40 % y 2 por ticker no están calibrados con el logbook**. Un perfil guardado antes de este cambio no trae `use_prob`: al cargarlo no filtra por probabilidad (sí aplica el tope por defecto, 2).

**Filtros del ticker en el perfil CDS (2026-11-03).** Antes el escaneo CDS decía aplicar «los filtros de empresa del criterio por defecto», pero ese criterio no activa ninguno (`criteria_from_settings`): no se descartaba ningún ticker por calidad. Ahora el perfil guarda los suyos, con las secciones «Calidad de la empresa» (beneficios 12 m > 0, trimestres con beneficios, FCF > 0, pasivo/patrimonio máx.), «Liquidez de las opciones» (tastytrade 1–5), «Precio del subyacente» y «Fuentes del Universo», leídos con el mismo parser que el Scanner de puts (`ui/cds_form.py::_read_ticker_filters`, `read_quality`). Se aplican antes de buscar spreads (`AppService._cds_ticker_filters`, lista de `TickerFilter`) y en el panel de impacto cuentan como un filtro más (claves `cds_profit`, `cds_quarters`, `cds_fcf`, `cds_lev`, `cds_liq`, `cds_price`, `cds_sources`; «Quitar» los apaga). Empiezan apagados: un perfil sin ellos escanea todos los tickers con calls guardadas. No hay «evitar resultados» (con DTE 200–500 siempre hay unos resultados antes del vencimiento y descartaría casi todo) ni los grados de solvencia del Universo. Los cierres del CDS tienen memoria propia (`cds_closes`), independiente del escaneo de puts.

**Límites de la integración:** los filtros propios de la put vendida (delta, técnicos, medias, soporte, IV Rank, calidad mínima de la put, resultados) no aparecen en el perfil CDS ni se aplican. «Todos los perfiles» sigue ignorando los perfiles CDS. «Todos los perfiles», el reconocimiento de puts por DTE (`classify.dte_range`) y la comparación de Comunidad/Radar ignoran los perfiles CDS; `/exits` los trata con las reglas de debit spread.

**La ventana es la del catálogo.** DTE (200–500), % OTM de la comprada (25–130) y los anchos que se pueden elegir son los de `scanner.cds` (config.yaml), como `scanner.candidates` para las puts: el formulario solo puede estrecharlos (un valor fuera se recorta y se avisa; un ancho que el catálogo no guarda se descarta). Débito, OI, spread de las patas, movimiento esperado, retorno máximo y SMA200 no afectan al catálogo y son libres.

**Valores revisados con el logbook (51 CDS reconstruidos del CSV, 2026-11-03):**
- **Débito máximo 2.150 $** (máximo del logbook: NVDA 250/350 dic. 2028, 2.145 $). Solo 5 de los 51 superan 650 $ (META 920/1000 1.040 $, NVDA ×2 2.140–2.145 $, RDDT 200/300 1.670 $, UBER 100/150 650 $); 9 están por debajo de 150 $ (mínimo 57 $, no se ha cambiado el mínimo).
- **Anchos:** 2–100 $ con mediana 50 $; los más frecuentes son 50 (13), 60 (7), 20 (4) y 100 (4). Son **proporcionales al precio**: strike < 50 $ → 3–25 $ (mediana 8), 50–100 $ → 15–30 $, 100–200 $ → 20–85 $, 200–400 $ → 50–100 $. Los anchos ≤ 10 $ solo aparecen en acciones baratas (RIVN, TSLL, ONDS, TTD, EOSE, GIS), así que no sobran: son los de esos tickers. Lista nueva: 5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 100 (antes 5/10/20/50, que solo cubrían 19 de los 51). **Coste del catálogo: +4 % de calls** (los strikes vendidos caen en la rejilla de los comprados), medido sobre las cadenas guardadas.
- **Ancho / strike de la comprada:** p10 8,7 %, mediana 22 %, p90 50 % (máximo 260 %: una acción de 8 $). `width_pct_min: 7` y `width_pct_max: 110` descartan anchos ridículos en acciones caras (p. ej. 5 $ sobre META) y enormes en las baratas.
- **SMA200:** el filtro (`sma200_below_pct_min`) queda **apagado por defecto** (era 10 %); la columna «vs SMA200 %» se calcula y se muestra siempre que haya 200 cierres (en verde si el precio está al menos un 10 % por debajo).
- **Mejor ancho:** `best_width` deja una fila por ticker, vencimiento y call comprada (la del menor movimiento necesario).
- **Limitación del emparejado:** el CSV no identifica los combos; las patas se emparejan por cuenta, ticker, vencimiento y minuto de apertura. 5 grupos tienen dos patas largas o cortas (entradas por duplicado de 1 contrato); los 4 débitos > 1.000 son CDS únicos.

**Efecto sobre los resultados (copia de la base, 2026-11-03):** con los valores nuevos salen 21.328 spreads de 284 tickers (antes 2.200 de 76); 6.080 con «mejor ancho»; con la SMA200 a −10 % bajarían a 9.554. La página muestra los 300 de menor movimiento necesario. META 930/1000 de 2028-01-21 (912 $, ×0,90 el movimiento esperado, −3,1 % bajo la SMA200) ya aparece.

## Pendiente

- Medir el coste del ciclo con `cds.enabled` y ajustar el rango si hace falta.
- Presets «MTO CDS 200–400» (más perfiles CDS: se crean desde el Scanner con «Guardar en perfil»), filtros de calidad propios del CDS (hoy los del criterio por defecto) y calibrar el filtro de movimiento necesario con el logbook.
- Añadir los CDS abiertos al Panel con el perfil MTO (las salidas ya existen en `other_mgmt`).

- Código: strikes vendidos por encima del precio en el Scanner (descuento ≤ 0 %) y presets «MTO PCS ITM» y «MTO CDS OTM».
- Registrar la pata de acciones tras una asignación para medir la rueda completa con exactitud (hoy solo se estima a partir de las calls cubiertas).
- Sustituir el CSV del logbook por `list_trades`/`get_portfolio` de la API de Tortuga y comprobar si trae las acciones asignadas.
- Repetir el análisis cuando los CDS abiertos venzan, para sustituir las marcas del modelo por resultados reales.
