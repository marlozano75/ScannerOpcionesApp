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
