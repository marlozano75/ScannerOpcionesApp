# Detección de suelos de mercado (investigación, 2026-11-06)

Objetivo: señales con las que detectar el suelo el mismo día o el anterior (para vender puts). Scripts en `scripts/suelos/`. **Solo investigación: ninguna señal está aún en la app.**

## Método
- 27 tickers líquidos (SPY, QQQ, IWM, AAPL, MSFT, NVDA, AMZN, META, GOOGL, TSLA, AMD, NFLX, JPM, BAC, XOM, INTC, MU, PLTR, COIN, SOFI, UBER, DIS, BA, XLF, GLD, TLT, SMH).
- Primera pasada con `daily_bars` (2023-2026, 20.000 barras). Segunda con Yahoo (2004-2026, 128.000 barras, incluye 2008, 2020 y 2022), descubrimiento 2004-2018 y validación fuera de muestra 2019-2026.
- Suelo = mínimo del día menor que el de los 7 días anteriores y posteriores, con rebote ≥ 2 ATR en los 10 días siguientes (base 8 % de las barras; 4 % si debe ser el propio día). La señal se calcula con el cierre de t y acierta si el suelo fue t o t-1.
- «Lift» = % de aciertos / base. Retornos a 10 días con cierre de la señal. Cada señal de un mismo día en varios tickers está correlacionada: los lifts son orientativos.

## Resultados (muestra completa 2004-2026; lift dentro de muestra / fuera de muestra)
| Señal | % suelo | Lift IS / OOS | Ret. 10d | Sin nuevo mínimo 10d |
|---|---|---|---|---|
| Keltner (EMA20 ± 2·ATR10): mínimo fuera y cierre dentro, o reversión | 30 % | 3,6 / 3,7 | +0,8 % | 29 % |
| Keltner reversión (cierre de ayer fuera, hoy dentro) | 36 % | 4,3 / 4,5 | +0,7 % | 33 % |
| Bollinger %b<0 / RSI5<20 / RSI14<30 | 19-20 % | 2,2-2,6 | +0,9-1,0 % | 17-20 % |
| Strat 2D-2U (sola) | 20 % | 2,4 / 2,4 | +0,8 % | 26 % |
| **Strat 2D-2U + Bollinger %b<0 el día anterior** | **47 %** | **5,7 / 5,8** | +0,5 % | 29 % |
| Keltner + volumen relativo ≥ 1,5 (hoy) | 36 % | 4,5 / 4,3 | +0,3 % | 34 % |
| Keltner + volumen relativo máx. 3d ≥ 2 | 37 % | 4,6 / 4,5 | +0,3 % | 34 % |
| Keltner + amplitud ≥ 35 % (valores bajo su Keltner) | 34 % | 3,8 / 4,6 | **+2,0 %** (64 % ganadoras) | 32 % |
| Keltner + pico de VIX reciente | 35 % | 4,4 / 4,1 | +0,8 % | 31 % |
| Keltner + SPY sobrevendido (reciente) | 33 % | 4,3 / 3,8 | +0,9 % | 32 % |
| Keltner + volumen ≥ 2 en 3d + pico de VIX | 44 % | 5,7 / 5,0 | +0,6 % | 40 % |
| Mogalef (regresión 3 de (O+H+L+2C)/5, σ de 7, ±2σ), mínimo fuera — solo muestra 2023-2026, no repetido en 22 años | 13 % | 1,6 | +1,3 % | 29 % |

## Conclusiones
1. Los lifts de la primera pasada se confirman fuera de muestra y en 22 años: Keltner con reversión y Strat 2D-2U tras un extremo (Bollinger/Keltner) son las mejores. La caída desde ATH (máximo de 252 días) sola, el 2D fallido, el martillo y el Mogalef (mi implementación) aportan poco.
2. **Volumen** (relativo a su media de 20 días): sube la precisión del suelo (30 % → 36-38 %) pero NO el rendimiento posterior: las señales con volumen de pánico llegan en caídas fuertes (MAE a 10 días −5,9 %) y rinden menos a corto plazo. Sirve para decidir «ya capituló», no para esperar un rebote inmediato.
3. **Contexto de mercado**: la amplitud (% de valores bajo su Keltner) es lo único que mejora a la vez precisión y rendimiento (≥ 35 %: +2,0 % a 10 días, 64 % ganadoras, pero solo 135 días distintos). VIX en pico/backwardation y SPY sobrevendido suben el acierto a 35-37 % y el rendimiento marginalmente.
4. Combinar demasiado reduce la muestra (decenas de casos) y degrada fuera de muestra (p. ej. Keltner + volumen + amplitud ≥ 20 %: 45 % → 34 %). Máximo recomendable: señal base + 1 filtro de volumen o mercado.
5. Ninguna señal evita el mínimo posterior: el peor precio de los 10 días siguientes queda de media 4-6 % por debajo del cierre de la señal. Para vender puts, strikes por debajo de esa zona. No es asesoramiento.
6. «ATE» de la petición se interpretó como ATH (máximo histórico/252 días); la caída medida en ATR desde el máximo de 10 días (2,5x) funciona mejor.

## Datos disponibles (comprobado el 2026-11-06)
- **tastytrade/DXLink** (`Candle`: open, high, low, close, **volume**, vwap, bid_volume, ask_volume): equities solo desde 2022-12-29 (~968 sesiones, lo que ya guarda la app); VIX desde 2018-04-02 (sin volumen). Su volumen está alineado con Yahoo (correlación de volumen relativo 0,88-0,93) pero es solo ≈30-40 % del consolidado (SPY 0,31, AAPL 0,40): úsese **relativo a su propia media**, no en valor absoluto, y no se mezcle con volumen de Yahoo.
- **Yahoo (yfinance, ya dependencia de la app)**: 2004-2026 con volumen consolidado; índices `^VIX`, `^VIX3M` (estructura de plazos), `^VIX9D`, `^VVIX`, `^SKEW` (sin volumen).
- **Volumen en la app (migración v44)**: `daily_bars.volume`, `BarRepo.volumes(ticker)`, `missing_volume`. Lo rellenan tastytrade y Yahoo. El histórico sin volumen se pide entero una vez, igual que se hizo con la apertura (v37): **la primera actualización diaria tras el cambio vuelve a descargar el histórico de toda la watchlist**.
- Sin cubrir: volumen/OI/put-call histórico de opciones del subyacente, intradía y IV Rank histórico (la app guarda barras de IV desde que empezó a pedirlas).

## Segunda ronda (2026-11-06): ATR, Mogalef y MACD, puntuación de suelo
«ATE» = ATR: la caída se mide en ATR (14) desde el máximo de 5/10/20 días (cierre o mínimo del día). Cuadrícula completa en `scripts/suelos/bt3.py` (datos Yahoo 2004-2026, 27 tickers, suelo = mínimo de ±7 días con rebote ≥ 2 ATR, acierto si fue hoy o ayer; base 8,2 %).

| Familia (mejor variante, n ≥ 300) | % suelo | Lift 2004-18 / 2019-26 | Ret. 10d |
|---|---|---|---|
| Caída en ATR: cierre ≥ 3 ATR bajo el máximo de 10 d | 18 % | 2,2 / 2,2 | +0,7 % |
| Caída en ATR: mínimo ≥ 3-4 ATR bajo el máximo de 5 d | 24-25 % | 2,9 / 2,8 | −0,1 a +0,6 % (caídas grandes siguen cayendo: MAE −5 a −6 %) |
| Mogalef original (regresión 3, σ 7, ±2σ): tocar la banda / reversión | 12 % / 23 % (n 766) | 1,5 / 1,3 y 3,1 / 2,3 | +0,7 % |
| Mogalef con regresión 10, σ 7, ±2σ, reversión | 28,5 % | 3,5 / 3,5 | +1,0 % |
| Mogalef con regresión 10 y σ 14 o 20, ±2σ, reversión | 31-32 % | 3,7 / 3,8-4,2 | +0,6-0,9 % |
| **Mogalef de canal sobre cierres (regresión de 20, σ de los residuos, ±2σ), reversión** | **36 %** | **4,2 / 4,8** | +0,7 % |
| Keltner reversión (referencia) | 30 % | 3,6 / 3,7 | +0,8 % |
| MACD 12/26/9: cruce al alza bajo cero · giro del histograma · divergencia sin confirmar | 7 % · 25 % · 24 % | 0,8 · 3,1 / 3,0 · 2,8 / 2,9 | +0,9 · +0,8 · +1,0 % |
| **MACD divergencia alcista** (nuevo mínimo de 20 d con MACD sobre su mínimo previo y MACD < 0) **+ cierre al alza al día siguiente** | **48-52 %** | **5,8-6,3 / ídem** | +1,0-1,3 % |

Control imprescindible: «mínimo de 20 días ayer + cierre al alza hoy», sin MACD, ya acierta el 35 % (patrón estructural: detecta el suelo de ayer). La divergencia MACD añade unos +13-17 puntos reales sobre el control, estable dentro y fuera de muestra.

### Puntuación propuesta (7 familias, cada una cuenta 1 si salta hoy o ayer)
1. `osc`: reversión de banda (Mogalef de canal 20/2σ, Mogalef 10/14/2σ o Keltner). 2. `atr`: cierre ≥ 3 ATR bajo el máx. de 10 d, o mínimo ≥ 3 ATR bajo el máx. de 5 d. 3. `macd`: divergencia alcista confirmada (12/26/9 u 8/17/9). 4. `strat`: 2D-2U. 5. `vol`: volumen relativo (máx. 3 d) ≥ 2. 6. `brd`: ≥ 20 % de la watchlist bajo su Keltner. 7. `rsi`: Bollinger %b < 0.

| Puntos | Probabilidad de que el suelo sea de hoy o ayer | 2004-18 / 2019-26 | n |
|---|---|---|---|
| 0-1 | 3 % - 9 % | — | 106.000 |
| 2 | 20 % | 19,7 / 19,6 | 11.110 |
| 3 | 27 % | 27,6 / 27,1 | 6.735 |
| 4 | 32 % | 32,9 / 31,1 | 3.549 |
| 5 | 43 % | 44,3 / 40,4 | 979 |
| 6 | 53 % | 52,9 / 52,2 | 135 |

Pares destacados: MACD + banda 53 % (n 1.464), MACD + volumen 59 % (n 288), Strat + banda 45 % (n 3.944). Probabilidad de que el SUELO sea el propio día: ~12 % con ≥ 3 puntos (la señal casi siempre detecta «hoy o ayer»).

### Confirmación al día siguiente (D+1: mínimo y cierre por encima de los de t)
Con ≥ 4 puntos en t y D+1 confirmado, P(t es el suelo) = 23 % (índices SPY/QQQ/IWM 27 %, n 303) y P(no se pierde el mínimo de t en los 10 días siguientes) ≈ 50 % (base 26 %). El retorno a 10 días desde D+1 no mejora la base (+0,5 % a +1,4 %): la utilidad es **colocar el strike por debajo del mínimo**, no anticipar rentabilidad. Sin confirmar: P(t es el suelo) ≈ 5 % y solo el 17 % no pierde el mínimo.

### Qué habría pasado en la bajada del 5-nov-2026 (SPY 766,8 → 733,0; QQQ −6,5 %; VIX 16,6 → 28,2)
- 30-oct y 2-nov: SPY ya puntuaba 4 y 5 (osc, volumen, amplitud, RSI); era un falso suelo: nuevo mínimo después (−2,4 % y −1,9 % hasta el 6-nov). De las 31 señales ≥ 4 de la semana con alguna sesión posterior, 14 (45 %) no rompieron su mínimo.
- 5-nov (día de la caída): SPY 4 puntos, QQQ 5 (caída en ATR, volumen 3,1x, amplitud 44 %, RSI; QQQ también banda). Sin MACD ni Strat todavía.
- 6-nov: SPY 5 y QQQ 5, volumen 0,85-1,15x (secado), mínimo del día (734,58) por encima del del 5-nov (732,71) y cierre al alza: confirmación D+1 cumplida. Según la tabla: ~27 % de que el 5-nov sea el suelo y ~50 % de que 732,7 no se pierda en 10 sesiones. No está resuelto: el suelo solo se conoce 7 días después.
- Valores con ≥ 4 puntos el 6-nov: SPY, QQQ, IWM, NVDA, TSLA, AMD, XOM, INTC, MU, PLTR, SMH.

## Tercera ronda (2026-11-06): datos intradía
**Profundidad disponible:** tastytrade/DXLink da velas de 5 y 30 min solo desde 2026-08-10 (~3 meses) y de 1 h desde 2026-01-26 (la petición agota el tiempo con frecuencia). Yahoo: 1 h desde 2023-12 (2 años), 5 y 15 min solo 60 días. Se usó Yahoo 1 h (27 tickers, 2023-12 → 2026-10, 3.048 días-ticker con puntuación ≥ 2 y 1.484 con ≥ 3) y 5 min para la semana actual. Muestra pequeña y solapada: orientativo.

### Qué mejora y qué no (puntuación diaria ≥ 3; base: P(t es el suelo) 13 %, mínimo de t no perdido en 10 sesiones 28 %)
| Característica | P(t es suelo) | Mínimo no perdido 10 d | Observación |
|---|---|---|---|
| **Rebote ≥ 1 ATR desde el mínimo hasta el cierre** (dato DIARIO, no intradía) | 30 % (n 174) | 63 % | En 22 años de datos diarios: puntuación ≥ 3 → 52 % suelo hoy-o-ayer (OOS 47 %) frente a 27 % sin rebote; mínimo no perdido 46 % vs 21 %; ret. 10 d ≈ 0 % (el rebote ya ocurrió) |
| Mínimo temprano (antes de las 11:30) | 14 % | 33 % (vs 20 %) | Mejora la persistencia, no el acierto del día |
| Mínimo aguantado en las 3 últimas horas | 14 % | 33 % (vs 19 %) | Idem |
| Mínimo tardío (15:30+) o cierre en el tercio inferior | 11-12 % | 20 % (vs 31-33 %) | Cierra en el mínimo: peor persistencia |
| Volumen de la última hora, último movimiento de la hora | sin efecto | — | — |
| **Confirmación de D+1 a las 11:30** (mínimo de las 3 primeras horas > mínimo de t y precio a las 11:30 > cierre de t) | **23 %** (vs 4,7 %) | **49 %** (vs 11 %) | Ret. 10 d desde el cierre de t +4,0 % (vs +1,1 %); mismas cifras que la confirmación diaria, 4-5 horas antes |

- El valor del intradía es **adelantar la confirmación**: la de D+1 a las 11:30 iguala en precisión a la del cierre diario. Lo que más aporta es descartar: sin confirmar a las 11:30, P(t es suelo) = 4,7 % y solo el 11 % conserva el mínimo (0,6 % si t cerró en el mínimo con mínimo tardío).
- Los demás patrones intradía (hora del mínimo, velas en V, volumen) casi no mueven el acierto del suelo de t; sí la persistencia del mínimo.
- Estable entre mitades (2023-12/2025-05 y 2025-06/2026-10): confirmación D+1 24,5 % y 20,6 %; rebote ≥ 1 ATR 34,6 % y 22,4 % (n 107 y 67).
- Perfil «mínimo tardío + cierre en el tercio inferior + puntuación ≥ 3» (el del 5-nov): 12,9 %; **con D+1 confirmado 29,6 % (n 135, mínimo no perdido 51 %, ret. 10 d +5,6 %, 76 % ganadoras); sin confirmar 0 %**.

### 5/6-nov-2026 con datos intradía
El 5-nov SPY, QQQ, SMH, NVDA, AMD, INTC y XOM hicieron su mínimo a las 15:55 y cerraron en él (rebote ≤ 0,04 ATR; TSLA, PLTR y MU rebotaron 0,15-0,24 ATR, ninguno llegó a 1 ATR): el perfil «sin rebote», que por sí solo no distingue suelo. El 6-nov a las 11:30 la confirmación se cumplió en 9 de 11 (todos salvo IWM y XOM, que perdieron el mínimo del 5-nov), por lo que el modelo daba ~30 % de que el 5-nov fuera el suelo en esos nueve y ~50 % de conservar el mínimo.

## Conclusión (2026-11-06): no hay ventaja para vender puts — no implementar como filtro
Las señales localizan zonas de suelo (lift 3-6x sobre la base del 8 %), pero **no mejoran la venta de puts**:
- Parte del acierto es estructural: «mínimo de 20 días ayer + cierre al alza hoy», sin indicador, ya acierta 35 %. El ~50 % solo se alcanza con combinaciones de pocos casos que se degradan fuera de muestra.
- El retorno a 10 días tras la señal (+0,5 % a +1,4 %) es el de cualquier día (+0,8 %).
- Prueba directa (entrada al cierre de D+1, vencimiento a 30 sesiones, 27 tickers, 2004-2026, `scripts/suelos`): probabilidad de vencer por debajo del strike / de tocarlo antes.

| Strike | Todos los días | Puntuación ≥ 3 y D+1 confirmado | Puntuación ≥ 4 y D+1 confirmado |
|---|---|---|---|
| −5 % | 21,8 % / 49,4 % | 21,7 % / 52,3 % | 19,9 % / 51,3 % |
| −8 % | 14,6 % / 32,9 % | 15,2 % / 35,9 % | 13,9 % / 34,6 % |
| −12 % | 8,5 % / 19,7 % | 9,2 % / 22,3 % | 8,5 % / 21,2 % |

  Solo 2019-2026: −8 % → 16,5 % / 37,3 % (base) frente a 17,1 % / 40,8 % (señal confirmada). Con la señal el riesgo es igual o algo peor: indica que ya cayó, no que vaya a dejar de caer. Que el mínimo del día de la señal se conserve más veces (~50 % frente a 26 %) no sirve con strikes a −8 % o más, muy por debajo de ese mínimo.
- **Sin medir:** tras una caída la IV está más alta y la prima es mejor; podría ser la ventaja real, pero no hay precios históricos de opciones. El Scanner ya lo capta con IV Rank y yield.

**Lo que queda útil:** el volumen diario guardado (v44) y la regla de descarte: una caída fuerte que no se confirma al día siguiente (mínimo de D+1 por debajo del de t, o precio bajo el cierre de t a las 11:30) casi siempre sigue cayendo (conserva el mínimo el 11 % de las veces).

## Cuarta ronda (2026-11-06): ¿hay señal de entrada para un CDS? (que el precio SUBA en los días siguientes)
Para un CDS no importa el suelo sino que el precio suba lo suficiente antes de caer. Métrica: entrando al cierre de t, **sube +1,5 ATR antes de bajar −1,5 ATR en 10 sesiones** (`win`; base **47,2 %**, pierde 39,6 %, el resto no resuelve), además de retorno a 10 días y extremo favorable. 128.000 días-ticker (27 tickers, 2004-2026), 173 señales (las de las rondas anteriores más tendencia, pullback en tendencia, rupturas de 20/55 días con volumen, cruces de EMA, MACD, Keltner superior, 2U con volumen, gaps y velas alcistas fuertes), con la misma muestra dividida 2004-2018 / 2019-2026.

- **Ninguna señal por ticker da ventaja útil.** Solo 2 de 173 mejoran la base ≥ 2 puntos en las dos mitades, y con 173 pruebas eso es lo esperable por azar. Rangos: señales de rebote 45-51 %, tendencia/ruptura 46-48 % (rupturas de 20/55 días: 46,3 % y 46,2 %, por debajo de la base), pullback RSI2<10 en tendencia 50,8 % (52,3 % antes de 2019, 48,6 % después). Las mejores y peores del ranking: Mogalef en banda −2,5σ 39-43 % (peor), `score ≥ 3` con rebote ≥ 1 ATR 43,8 % (peor, pese a ser la mejor para localizar el suelo).
- **Confirmación intradía de D+1 (11:30):** no mejora la entrada. Con puntuación ≥ 3, entrar a las 11:30 de D+1 rinde +2,0 % hasta t+10 si se confirmó y +2,5 % si no (base +1,1 %). Confirmar sirve para descartar suelos, no para elegir entradas.
- **Lo único con ventaja: amplitud de mercado** (≥ 20 % de la watchlist con el cierre bajo su Keltner, es decir, un día de capitulación general). Es una condición de MERCADO, no del ticker: 395 días (7 % de los días) con `win` 52,6 % frente a 46,8 % y retorno medio a 10 días +1,47 % frente a +0,75 %; en 2023-2026 (entrando a las 11:30 de D+1) +3,3 % frente a +1,1 % y el 69 % positivos. Pero es irregular por régimen: funciona en recuperaciones en V (2009, 2014, 2015, 2018, 2021, 2023, 2024, 2026) y falla en caídas que continúan (2020 −4,4 % a 10 días, 2022 −0,5 %). Son pocos episodios independientes (días agrupados), así que es orientativo.
- Valor práctico limitado: un CDS de 200-400 DTE depende más de la tesis y de la IV de entrada que de ganar o perder 0,7 puntos en 10 días. Un posible uso: informar en el CDS (no filtrar) si el mercado está en capitulación (≥ 20 % de la watchlist bajo Keltner). El 2026-11-06 estaba en 26 %. No implementado.

## Quinta ronda (2026-11-06): Fair Value Gap (LuxAlgo) y Volume Profile
Misma muestra y métrica que la cuarta ronda (`win` = sube +1,5 ATR antes de bajar −1,5 ATR en 10 sesiones; base 47,2 %; suelo hoy-o-ayer, base 8,2 %). Implementación (`scripts/suelos/fvg_vp.py`):
- **FVG de LuxAlgo** (lógica del Pine público): alcista si `low[0] > high[2]`, `close[1] > high[2]` y el cuerpo de la vela 1 supera el umbral automático (2 × media acumulada de |cierre−apertura|/apertura); bajista, simétrico. La zona sigue abierta hasta que un cierre la mitiga (bajo su fondo la alcista; sobre su techo la bajista) o pasan 120 sesiones. Señales: FVG nuevo, retest de una alcista abierta (mínimo dentro de la zona, con o sin vela alcista), cierre dentro de la zona, FVG bajista mitigado.
- **Volume Profile** con velas diarias: el volumen de cada vela se reparte uniformemente en su rango [mín, máx] sobre 40 niveles de las últimas 60 o 120 sesiones (hasta ayer); POC (nivel de más volumen) y área de valor del 70 % (VAL/VAH). Señales: reclama VAL (cierre de vuelta dentro), toca VAL y aguanta, cruza o apoya en POC, rompe VAH, lejos bajo POC (≥ 1,5 ATR) con giro.

**Resultado para CDS: ninguna señal mejora la base** (todas 43-48 % frente a 47,2 %). Retest de FVG alcista 47,0 % (48,3 % antes de 2019, 45,6 % después), con tendencia 48,2 % (50,7 / 45,4), con amplitud ≥ 20 % 49,8 %; FVG nuevo 44,6 %; FVG bajista mitigado 43,6 %; VP60 apoyo en POC 47,6 %, cruce de POC 47,2 %, ruptura de VAH 47,1 %; reclamar VAL 44-45 %, y con amplitud ≥ 20 % 39-42 % (peor). Retornos a 10 días 0,4-1,0 % frente a 0,8 % de la base.

**Para localizar suelos sí hay algo, pero no se traduce en subidas:** reclamar el VAL del perfil de 60 sesiones acierta el suelo 21 % (lift 2,6x; VP120 19 %), tocarlo y aguantar 18 % (2,1x), estar bajo VAL 15 % (1,9x), cerrar dentro de una FVG alcista 9 % (1,1x), retest de FVG alcista 11-15 % (1,4-1,8x). Es del orden de las bandas de volatilidad (Keltner 30 %) o menos, y como en las rondas anteriores el precio después no sube más que cualquier otro día.

**Límites:** el FVG de LuxAlgo es una herramienta visual para elegir zonas a mano; el backtest solo mide la regla mecánica. El Volume Profile está aproximado con velas diarias (el real usa ticks/intradía); con 1 h de Yahoo solo hay 2 años y los resultados de VP120/FVG por mitades ya muestran inestabilidad (p. ej. retest con tendencia 50,7 % → 45,4 %).

## Sexta ronda (2026-11-06): ¿alguna combinación con más del 65 % de que el precio se recupere en 10 días?
Búsqueda exhaustiva de pares (22.578) sobre 212 señales (todas las anteriores, VIX/VVIX/estructura de plazos, amplitud, FVG, Volume Profile…), descubriendo con 2004-2018 y exigiendo n ≥ 300 (150 en 2019-2026). Dos definiciones de «recuperarse»: **A** = cierra por encima del precio de entrada a los 10 días (base 55,9 %); **B** = toca +1,5 ATR en algún momento de las 10 sesiones (base 51,9 %). `scripts/suelos/srch.py`.

- **Con A solo 2 combinaciones superan el 65 % en las dos muestras**, y con B 6. Ninguna señal individual lo hace.
- Todas comparten el mismo ingrediente: **estrés de mercado + sobreventa del ticker**. Estrés = VIX por encima de VIX3M (backwardation), VVIX alto o ≥ 35-50 % de la watchlist bajo su Keltner; sobreventa = MACD en su 5 % inferior del último año, o RSI2 < 10 en tendencia.
- Mejores y su robustez (filas = ticker-día; un voto por día baja el porcentaje porque los tickers del mismo día están correlacionados):

| Combinación | Definición | P (filas) | P (un voto por día) | Días / episodios | Años ≥ 60 % | Ret. 10 d medio / peor 10 % |
|---|---|---|---|---|---|---|
| Pullback RSI2<10 en tendencia + VIX>VIX3M | B | **68,6 %** (2004-18 69,5 / 2019-26 66,4) | 67,8 % | 246 / 82 | 16 de 20 | +1,95 % / −7,7 % |
| MACD(5,35,5) en extremo + ≥ 35 % de la watchlist bajo Keltner | B | 66,2 % | 61,8 % | 161 / 57 | 16 de 21 | +2,0 % / −10,0 % |
| MACD(12,26,9) en extremo + VIX>VIX3M | A | 65,4 % | 60,0 % | 452 / 61 | 15 de 19 | +2,5 % / −8,6 % |
| MACD(12,26,9) en extremo + VVIX alto | A | 64,9 % | 60,5 % | 692 / 82 | 15 de 19 | +2,0 % / −7,6 % |
| MACD(12,26,9) divergencia alcista + VVIX alto | A | 65,9 % | 61,2 % | 414 / 101 | 11 de 19 | +2,8 % / −6,3 % |

- Solo la primera supera el 65 % con un voto por día y la mayoría de los años. En esa misma muestra la carrera «sube +1,5 ATR antes de bajar −1,5 ATR» da 60,0 % (pierde 35,0 %) y el 63,5 % cierra por encima a los 10 días.
- **Cautelas:** (1) la mayor parte de la ventaja viene del régimen: el VIX en backwardation ya da 59,8 % (A) y 55,3 % (B) por sí solo; (2) se probaron 22.578 pares por definición, así que el azar produce candidatos; la validación en 2019-2026 reduce pero no elimina ese riesgo; (3) «toca +1,5 ATR» no es el beneficio del CDS (necesita llegar al strike/objetivo y a tiempo); (4) el peor 10 % de los casos pierde un 7-10 % a 10 días, precisamente en los momentos de más estrés; (5) son pocos episodios independientes (48-82), concentrados en crisis y rebotes.
- **Medición por episodio del pullback RSI2<10 en tendencia + VIX>VIX3M (2026-11-09, `scripts/suelos/episodios.py`; datos regenerados desde Yahoo, reproducen 975 filas / 246 días / 82 episodios).** Episodio = racha de días con señal separados por más de 10 sesiones (con 3 días serían 114, con 5 100, con 20 65). RSI2 es del ticker; VIX>VIX3M es del mercado (igual para toda la watchlist ese día). Definición B (base 51,9 %): P por filas 68,6 %, por día 67,8 %, **por episodio 71,7 %** (IC95 % bootstrap 64,9-78,1; mediana 80 %; 65 de 82 episodios ≥ 50 %, 6 sin ningún acierto; 2004-18 71,6 % con 57 episodios, 2019-26 71,9 % con 25). **Solo el primer día de cada episodio (lo operable en tiempo real): 63,3 %**, unos 11 puntos sobre la base y no 17: los días siguientes de la racha ya vienen tras una caída larga. Los episodios grandes rinden peor (24-feb-2020, 69 filas: 42 %; jul-2011: 25 %; 2008: 35 % de media en 6 episodios). Definición A (base 55,9 %): filas 63,5 %, día 67,2 %, episodio 69,8 % (IC95 % 62,7-76,5), primer día 64,7 %. Sigue siendo el mejor de 22.578 pares, así que parte puede ser selección.

- **Componente de estrés del propio ticker: Keltner (2026-11-09, `scripts/suelos/episodios_keltner.py`).** Mismo pullback RSI2<10 en tendencia + VIX>VIX3M, separado por si el cierre del ticker está bajo su Keltner (EMA20 − 2·ATR10). Definición B (base 51,9 %), P por episodio / primer día: todos 71,7 % / 63,3 % (82 episodios); **bajo Keltner 76,2 % / 69,0 %** (52 episodios; 2004-18 74,4 %, 2019-26 79,3 %); no bajo Keltner 68,3 % / 59,8 % (78). Mirar los 3 días previos da lo mismo (76,1 %). Gradiente por terciles de (cierre − Keltner)/ATR10: 76,2 % / 73,5 % / 66,7 % (más bajo = mejor). **Con la definición A no hay efecto** (67,7 % bajo frente a 68,6 % no bajo): la mejora está en tocar +1,5 ATR, no en cerrar por encima a 10 días. Sin backwardation también ayuda: pullback + bajo Keltner 68,5 % por episodio (170 episodios; 67,4 % en 2004-18, 70,5 % en 2019-26) frente a 59,4 % del pullback sin ese filtro. Ingredientes sueltos (P filas / P día, comparables): pullback 56,8 / 54,4; VIX>VIX3M 55,3 / 55,0; bajo Keltner 54,5 / 49,8; el patrón combinado 68,6 / 67,8. **Cautelas:** 52 episodios (19 en 2019-26) y sin intervalo de la diferencia; no es una prueba independiente (mismo periodo en que se hizo la búsqueda de 22.578 pares), tomarlo como hipótesis; la «P por episodio» de las señales sueltas NO es comparable (con miles de días con señal los episodios se funden en rachas de meses: bajo Keltner solo da 67,1 % por episodio pero 49,8 % por día y 41,7 % el primer día), usar P por fila o por día para compararlas.

- **Intervalo de confianza de la diferencia bajo/no bajo Keltner (2026-11-09, `scripts/suelos/ci_keltner.py`; matiza la viñeta anterior: el efecto es más débil e incierto).** Definición B: diferencia de P por episodio +8,5 pts, IC95 % bootstrap por episodios −0,9 a +17,8 (P(dif ≤ 0) = 0,038); pareada dentro de cada episodio (46 con ambos grupos) +11,2 pts (mediana +4,8), IC95 % +2,1 a +20,2; **por filas solo +3,0 pts (300 contra 675), IC95 % por cluster −5,4 a +12,5**; permutación dentro de cada episodio p = 0,0015; por filas +2,3 pts en 2004-18 y +5,1 en 2019-26. La mejora por episodio sale mayor porque los episodios con pocos casos pesan igual y en ellos la ventaja es mayor; en los grandes casi no hay diferencia. **Definición A: sin efecto** (−0,2 pts por episodio, IC95 % −10,9 a +10,2). Lectura: indicio débil solo para «tocar +1,5 ATR», con un valor central de +3 a +8 pts y no +17; la prueba se eligió después de ver los datos, así que la permutación no es una prueba estricta. No justifica añadir la Keltner como filtro en la app.

- **Validación fuera de muestra con 2.179 tickers (2026-11-09, `scripts/suelos/validacion_*.py`).** Tickers de la watchlist distintos de los 27 originales, Yahoo 2004-2026, 8,7 millones de filas; se descartan los de menos de 400 velas o cuyo cierre no coincide con `daily_bars` (símbolos distintos). El script reproduce los resultados originales con los 27. Pullback RSI2<10 en tendencia + VIX>VIX3M, definición B (base 47,6 % en esta muestra): **P por filas 58,2 % (68,7 % con los 27), por episodio 64,6 % (IC95 % 60,4-68,7; 71 episodios; 65,4 % en 2004-18, 63,2 % en 2019-26), primer día 58,3 %** (+10,7 pts sobre la base). El 68,6 % de la búsqueda estaba inflado por selección: expectativa realista ≈ 58 % por filas / 64 % por episodio. **Casi todo lo aporta el régimen:** VIX>VIX3M solo da 51,4 % por filas y 61,7 % por episodio (primer día 54,7 %). Comparado con todos los tickers del MISMO día de backwardation, el pullback añade solo **+2,2 pts por episodio (IC95 % +0,5 a +3,8)** y, dentro del pullback, estar bajo la Keltner del ticker **+3,9 pts (IC95 % +1,5 a +6,4; con la definición A +2,2, IC95 % −0,4 a +4,7)**; la Keltner sola sin RSI2 no aporta (−0,9, IC95 % −2,5 a +0,6). Años en contra: 2008 42,1 % (todos 41,7 %), 2024 56,9 % (todos 62,8 %), 2026 35,8 % (todos 48,4 %). Cautelas: sesgo de supervivencia (watchlist de hoy), señales concentradas en pocos días de crisis (mediana 40 tickers con señal por día, máx. 810; P por día 53,1 %), y la P por episodio de «pullback solo» (2 episodios) no es interpretable. Pasos: `validacion_fetch.py` → `validacion_filtro.py` → `validacion_muestra.py $D yf_val_ok.pkl` (necesita `yf.pkl` de la ronda original; escribe `G_yf_val_ok.pkl`) → `validacion_control.py`.

- Estado el 2026-11-06: VIX/VIX3M = 1,031 (backwardation), 26 % de la watchlist bajo Keltner, MACD(12,26,9) en extremo + backwardation en AMD, GOOGL, INTC, MU, NVDA, QQQ y SMH; el pullback RSI2<10 en tendencia + backwardation no estaba activo en ningún ticker ese día (sí PLTR y XOM el 5-nov).

## Séptima ronda (2026-11-06): The Strat completo para CDS
Patrones sobre velas diarias (1 interior, 2U, 2D, 3 exterior): 2D-2U, 2D-2D-2U, 3-2U, 3-1-2U, 2D-1-2U, 2U-1-2U, 1-2U, 1-1-2U, 2U-2U, 2U-2U-2U, 2D fallido (con 2U siguiente), martillo y el control bajista 2U-2D; continuidad entre temporalidades (FTFC: vela diaria, semana y mes en curso todas verdes o todas rojas) y barra semanal/mensual 2U, 2D o 3. Misma muestra (128.000 ticker-día, 2004-2026). `scripts/suelos/strat*.py`.

- **Al cierre de la vela de señal ningún patrón mejora la base** (P cierra por encima a 10 d 55,9 %; toca +1,5 ATR 51,9 %): todos entre 49,9 % (3-1-2U con FTFC) y 57,8 % (2D-1-2U con semana 2U), sin consistencia entre mitades. El control bajista 2U-2D da 56,5 %, igual que los alcistas. FTFC alcista 56,3 % (57,0 % con tendencia) y FTFC bajista 55,1 %. Con estrés (VIX>VIX3M): 2D-2U 57,8 %, 3-2U 60,4 % (n 280), cualquier 2U de giro 58,1 % (66,1 % solo después de 2019): no es una mejora fiable.
- **Para localizar suelos** (no para subir): 2D-2U 19,8 %, 2D-2D-2U 30,5 %, 2D-2U tras Bollinger 47,3 %, como en la primera ronda.
- **Entrada como se opera en Strat** (stop de compra en el máximo de la vela interior; entra el día siguiente si rompe arriba sin romper abajo; resultado medido desde el precio de entrada): base «cualquier día con ruptura del máximo de ayer» 57,4 % cierra por encima a 10 días, win (+1,5 ATR antes que −1,5 ATR) 50,2 %. **2D-1 (vela interior tras una 2D) 58,5 % y win 54,0 %** (n 2.763; 2004-18 58,3 % / 2019-26 58,7 %; pierde 34,2 % frente a 37,0 %); 2D-2D-1 58,5 % y 52,5 %; 2U-1 58,0 % y 49,4 %; 3-1 55,0 %. Mejora real pero pequeña (+1 punto en cierre, +3-4 puntos en la carrera), muy lejos del 65 %.
- Conclusión: The Strat no aporta una ventaja de entrada para el CDS; lo más cercano es 2D-1 con disparo alcista.

## Octava ronda (2026-11-08): patrones de Bulkowski (doble suelo e HCH invertido)
Prueba de la idea «el suelo bien formado, operado solo tras el cierre sobre la clavícula, mejor con gap y a favor del mercado» (`scripts/suelos/patrones.py`; 2.405 dobles suelos y 501 HCH invertidos en 27 tickers, 2004-2026; mismas métricas de CDS a 10 sesiones).

- **Sin ventaja:** doble suelo confirmado 57,5 % cierra por encima a 10 d (base 55,9 %), con gap 58,3 %; HCH invertido 55,5 % (con gap 52,3 %). Toca +1,5 ATR 52,1 % / 47,7 % (base 51,9 %). SPY>SMA200 57,3 %, SPY<SMA200 58,6 %.
- **Combinaciones:** con VIX>VIX3M 57,3 % (2019-26: 50,0 %, n = 96); con ≥ 20 % de la watchlist bajo Keltner 62,0 % (n = 50, 2019-26: 53 %). Añadirlo al pullback RSI2<10 + backwardation lo empeora (68,6 % → 62,1 %; carrera 60,0 % → 53,6 %).
- Las cifras de Bulkowski son subidas máximas de operaciones ideales, no la probabilidad de estar arriba a 10 sesiones. Registrado como R9 en la pestaña Backtest.

## Indicador de capitulación en el Panel (2026-11-08)
Único resultado llevado a la app, solo informativo (no filtra ni puntúa nada): en la tarjeta del VIX del Panel, `scanner/capitulation.py` combina (1) **amplitud**: % de una cesta fija de 27 valores líquidos (`scanner.capitulation.tickers`, la de la investigación) cuyo último cierre está bajo EMA20 − 2·ATR10, con umbral `breadth_min_pct` (20 %), y (2) **backwardation**: VIX por encima del primer futuro VX. Con las dos: *Capitulación de mercado*; con una: *parcial*. Medido en la investigación con VIX>VIX3M y amplitud ≥ 20 % (174 días, 2004-2026): cierra por encima a 10 sesiones el 61,3 % (base 55,9 %), toca +1,5 ATR el 64,7 % (51,9 %), gana la carrera el 56,0 % (47,2 %); solo backwardation 59,8 %, solo amplitud 58,5 %. **VIX3M:** tastytrade no lo da (sondeado por DXLink el 2026-11-08 sin respuesta), así que `fetch_vix3m` (`marketdata/yahoo.py`) lo pide a Yahoo (`^VIX3M`, ~15 min de retraso) en cada refresco del VIX y se guarda con la caché del VIX (`VixData.vix3m`); es la misma serie del backtest. Si Yahoo falla, el indicador usa el futuro a un mes como sustituto (parecido pero no medido) y el texto lo dice. Es efecto del régimen de mercado, no hay ventaja por ticker.

## Marca «◆D» (divergencia MACD) por ticker (2026-11-08)
`scripts/suelos/cap_ticker.py` (ronda R11): dentro de los días de capitulación la divergencia alcista del MACD(12,26,9) (mínimo de 20 sesiones sin nuevo mínimo del MACD, MACD < 0) cierra por encima a 10 sesiones el 72,1 % (383 filas, 108 días; 2004-18 70,0 % / 2019-26 75,7 %; toca +1,5 ATR 69,5 %; gana la carrera 60,3 %), frente al 61,3 % de la capitulación sola; las otras tres configuraciones dan 69-72 % y fuera de capitulación 52-54 %. App: `scanner/macd_divergence.py`, `AppService.div_marks` y el global de plantillas `div_flag` (`ui/web.py`) ponen «◆D» junto al ticker en el Scanner (puts, spreads y CDS), el escaneo de todos los perfiles, el Panel, la Watchlist, el Universo, Comunidad, MarketRadar y el Feed, **solo cuando la capitulación es completa**. Además, el Scanner (puts, spreads y CDS) tiene la columna ordenable «Div. MACD» (◆D o «—») y, en «Técnicos de entrada», la casilla «Solo con ◆D» (parámetro `use_div` de la URL; filtra después del escaneo con `AppService.keep_div` / en `routes_cds.page`, no se guarda en los perfiles, no entra en el panel de impacto y avisa si no hay capitulación porque entonces no queda ningún ticker). El escaneo de todos los perfiles lleva la columna pero no la casilla (usa los filtros guardados de cada perfil). Se calcula sobre las velas guardadas (260 días) para todos los tickers y se reutiliza entre peticiones (`_memo`). Cautelas: ~100 señales probadas, ~110 días independientes.

## Novena ronda (2026-11-08): cruce MACD 6-20-9 con precio sobre la EMA10
`scripts/suelos/macd620.py`. Setup «620»: MACD(6,20,9) cruza al alza su señal y el cierre está sobre la EMA10. 5.991 señales: 56,9 % cierra por encima a 10 d (base 55,9 %), toca +1,5 ATR 52,1 % (base 51,9 %). El mejor filtro es pullback en tendencia (c>SMA200, SMA50>SMA200) con MACD<0: 60,0 % (n = 1.375, 249 episodios; pierde 36,0 % frente a 39,6 %). Con estrés de mercado las muestras son pequeñas (VIX>VIX3M 60,7 %, n = 389; amplitud ≥ 20 % 63,1 %, n = 111) y no mejora la combinación pullback RSI2 + backwardation. Registrado como R10 en la pestaña Backtest.
