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
