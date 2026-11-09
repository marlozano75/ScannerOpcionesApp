# Investigación de suelos de mercado (2026-11-06)

Scripts sueltos (no forman parte de la app; no tocan `data/app.db`). Resultados y conclusiones en `docs/deteccion-suelos.md`.

```bash
D=ruta/a/una/carpeta/temporal
.venv/Scripts/python scripts/suelos/fetch_yf.py $D      # Yahoo 2004-2026: 27 tickers líquidos + ^VIX ^VIX3M ^VIX9D ^VVIX ^SKEW -> $D/yf.pkl
.venv/Scripts/python scripts/suelos/bt2.py $D           # señales y etiquetas (suelo = mínimo de ±7 días con rebote ≥ 2 ATR en 10 d) -> $D/A.pkl
.venv/Scripts/python scripts/suelos/ev3.py $D           # tabla por señal, muestra 2004-2018 / 2019-2026
.venv/Scripts/python scripts/suelos/fetch_tasty.py $D/t.pkl SPY,VIX,AAPL 15   # comprobación de las velas de DXLink (volumen, años disponibles)
```

Segunda ronda (ATR, variantes de Mogalef, MACD y puntuación): `bt3.py $D` (-> `$D/B.pkl`, cuadrícula de señales), `ev4.py $D` (ranking por familia, muestra 2004-2018 / 2019-2026) y `ev5.py $D` (puntuación de 7 familias y pares). El análisis de la semana del 30-oct al 6-nov y la confirmación al día siguiente se hicieron con fragmentos sueltos sobre `B2.pkl` y no están guardados.

Tercera ronda (intradía): `fetch_intra.py $D` (Yahoo 1 h 730 días y 5 min 60 días de los 27 tickers), `intra1.py $D` (une características intradía con la puntuación de `ev5.py` -> `$D/J.pkl`), `intra2.py $D` y `intra3.py $D` (tablas por característica, mitades de muestra y equivalente diario en 22 años). La tabla de la semana del 5-nov (SPY, QQQ, SMH…) se hizo con un fragmento suelto no guardado.

Cuarta ronda (entrada en un CDS): `cds1.py $D` (resultados a 10 sesiones: carrera «sube +1,5 ATR antes de bajar −1,5 ATR», extremos y retornos; señales de tendencia/ruptura/momentum; usa `B2.pkl` de `ev5.py`) y `cds2.py $D` (ranking de 173 señales, muestra 2004-2018 / 2019-2026). Los análisis de amplitud por día/año y de la confirmación intradía con entrada a las 11:30 se hicieron con fragmentos sueltos.

Quinta ronda (Fair Value Gap de LuxAlgo y Volume Profile): `fvg_vp.py $D` (señales -> `$D/FV.pkl`; necesita `$D/CDS.pkl` de `cds1.py`) y `fvg_ev.py $D` (tabla con la métrica de CDS y la de suelos).

Sexta ronda (búsqueda de combinaciones con P > 65 %): `srch.py $D` (todos los pares de 212 señales, descubrimiento 2004-2018, validación 2019-2026, detalle por días/episodios/años; necesita `CDS.pkl`, `A.pkl` y `FV.pkl`).

Séptima ronda (The Strat completo): `strat.py $D` (tipos de vela 1/2U/2D/3, patrones, barra semanal y mensual en curso y FTFC -> `$D/STRAT.pkl`), `strat_ev.py $D` (métrica de CDS al cierre de la señal) y `strat_trig.py $D` (entrada en la ruptura del máximo de la vela interior, como se opera en Strat).
Octava ronda (patrones de Bulkowski): `patrones.py $D` (doble suelo e HCH invertido con cierre sobre la clavícula, gap, contexto SPY y suma a las mejores señales).
Novena ronda (cruce MACD 6-20-9 + EMA10): `macd620.py $D`.
Marca por ticker (R11): `cap_ticker.py $D` (señales del ticker dentro de los días de capitulación).

Sexta ronda (medias y soportes, 2026-11-08): `medias_soportes.py [salida] [n]` (toques y rebotes de 8 medias y soportes horizontales por ticker, con validación fuera de muestra; escribe `medias.csv`, `soportes.csv`, `valida.txt`), `medias2.py` (persistencia de la tasa de rebote por ticker y media: ventanas, bandas, confirmación y régimen; el régimen se evalúa el día anterior al toque) , `soportes2.py` (afinado de la banda del soporte 0,5-3 %) y `soportes3.py` (filtro de soporte actual frente a los de mínimos). Leen `data/app.db` en solo lectura. Resultado aplicado en la app: columnas «Soporte» y «Strike/Sop.» (`docs/scanner-estrategia-spreads.md`).
Medición por episodio (2026-11-09): `episodios.py $D` (pullback RSI2<10 en tendencia + VIX>VIX3M: P por fila, día, episodio y primer día de cada episodio, IC por bootstrap, mitades y años; necesita `yf.pkl` y `CDS.pkl`, que salen de `fetch_yf.py`, `bt3.py`, `ev5.py` y `cds1.py`).
Mismo pullback separado por la Keltner del ticker (2026-11-09): `episodios_keltner.py $D` (necesita `yf.pkl` y `CDS.pkl`).
Intervalo de confianza de esa diferencia (2026-11-09): `ci_keltner.py $D` (bootstrap por episodio, pareado, por filas con cluster y permutación; necesita `yf.pkl` y `CDS.pkl`).
Validación fuera de muestra con 2.179 tickers (2026-11-09): `validacion_fetch.py $D` (Yahoo, lee la watchlist de `data/app.db` en solo lectura; desde la raíz del repo), `validacion_filtro.py $D` (descarta símbolos que no coinciden con la BD), `validacion_muestra.py $D yf_val_ok.pkl` (P por filas, día, episodio y primer día; necesita `yf.pkl`) y `validacion_control.py $D` (frente al control del mismo día).
Componente de volatilidad propia (2026-11-09): `validacion_volatilidad.py $D` (percentil del ATR% y cociente frente a su media; resultado negativo; necesita `G_yf_val_ok.pkl`).
Reglas de medias como soporte (2026-11-09, R12): `medias_reglas.py $D` (Yahoo, 27 tickers; necesita `yf.pkl`) y `medias_reglas.py $D db` (velas de `data/app.db`, solo lectura): Schwartz EMA10, 8 EMA de bigwavechartist y primer toque de la SMA5 tras ruptura, frente al mismo día, a la tendencia sin toque y a un nivel placebo.
Medias como medida de tendencia (2026-11-09, R13): `medias_tendencia.py $D` y `medias_tendencia.py $D db` (persistencia, medias apiladas, pendiente, R² y extensión frente al mismo día; colchón de 2 ATR y fijo del 10 %).
Tiempo sobre las medias por ticker (2026-11-09, R14): `medias_tiempo.py $D` y `medias_tiempo.py $D yf` (% de sesiones sobre cada media, rachas, cruces, sector y persistencia entre mitades; CSV por ticker).
