# Estrategia de venta Tastytrade

Especificación del perfil **Tastytrade** de ScannerOpcionesApp y lista priorizada de cambios
para adaptarlo a una cuenta pequeña que opera sobre todo **spreads de puts**.

Sustituye a `estrategia-venta-puts.md`.

---

## 0. Restricciones de implementación

Cualquier cambio de esta lista debe respetar la arquitectura actual:

- **No se añaden fuentes de datos ni llamadas nuevas a TWS.** Todo se calcula con datos que ya
  se descargan (market metrics, cadena y cotizaciones DXLink, velas, what-if existentes).
- La app sigue siendo de **solo lectura**: todo son filtros y avisos, nunca órdenes.
- Toda clave nueva va en el modelo pydantic de config (frozen, `extra="forbid"`) y en
  `config.yaml`, y debe poder editarse en `/scanner/config` si afecta al perfil.
- Persistencia: solo migraciones de añadir en `db.py::MIGRATIONS`.
- Sin lógica de negocio en la UI. Los cálculos van en `scanner/engine.py` o en servicios.
- Cada cambio con tests sobre `FakeGateway`.
- Los filtros nuevos deben aparecer en «Activados/Desactivados», en el panel
  «¿Cuánto descarta cada filtro?» y viajar en la URL, como los existentes.
- **Antes de implementar cada punto, comprobar si ya existe** total o parcialmente.

---

## 1. La estrategia (perfil Tastytrade)

Vender volatilidad cara cuando la caída se está calmando, con el strike vendido por debajo
de un suelo lógico (soporte, movimiento esperado y distancia en ATR), limitando el riesgo
máximo por operación al tamaño de la cuenta.

| Capa | Condición | Valor por defecto |
|---|---|---|
| Universo | Calidad para puts desnudas; liquidez para spreads | Ver P1 |
| Volatilidad | (IV Rank > 30 % **o** IV Pct > 50 %) **y** IV/HV30 ≥ 1,0 **y** cambio IV 5 días ≤ 0 | Ideal: IV Rank y Pct > 50 %, IV/HV > 1,2 |
| Timing | Al menos 1–2 de: cerca de MA50/soporte, RSI rebotando, Bollinger inferior | «al menos N» = 1 |
| Strike | Delta −0,20 a −0,30 (desnuda) / −0,20 a −0,35 (spread); ≥ 2 × ATR; por debajo del movimiento esperado | |
| Vencimiento | DTE 30–45; sin resultados antes del vencimiento | |
| Spread | Crédito ≥ 1/3 de la anchura (mínimo 25 %) | Retorno sobre riesgo ≥ 0,50 (mínimo 0,33) |
| Gestión | Recoger al 50 %; revisar a 21 DTE; stop y riesgo de pin en spreads | Ver P6 |

Enrutado (modo «Ambos», ya existente): put desnuda si `strike ≤ max_naked_strike` y el
ticker pasa la calidad; si no, su spread.

---

## 2. Cambios priorizados

### P1 — Universo apto para spreads (prioridad alta) — IMPLEMENTADO 2026-10-16

> Hecho: `scanner.quality.etf_index_symbols` (ETF exentos), fuentes aptas para spreads (liquidez y precio en vez de calidad, solo modo Spread)
> y la rutina semanal del universo (`scripts/actualizar_universo_bps.ps1`). La config final usa `spread_universe.min_option_liquidity`,
> `price_min` y `price_max` en lugar de `price_range`. Ver CLAUDE.md.

**Objetivo:** ampliar los tickers escaneados más allá de las empresas de calidad, porque en un
spread el riesgo está definido y lo que importa es la liquidez de las opciones.

**Qué hacer:**
1. Permitir marcar una fuente o lista del Universo como **apta para spreads**. Sus tickers
   no pasan los filtros de calidad (beneficios, FCF, solvencia), pero sí filtros de liquidez:
   - Liquidez de opciones de tastytrade ≥ `spread_universe.min_option_liquidity`.
   - Precio del subyacente dentro de `spread_universe.price_range`.
2. En el Scanner, un ticker que solo pertenece a fuentes aptas para spreads **solo puede
   aparecer en modo Spread** (en «Ambos», nunca como put desnuda).
3. **ETFs e índices** (SPY, QQQ, IWM, DIA, XSP, ETFs sectoriales…): eximirlos de los filtros
   de calidad, igual que los sectores exentos, porque no tienen EPS ni datos en EDGAR.
   Identificarlos con una lista en config (`etf_index_symbols`) o por el tipo de instrumento
   si el proveedor ya lo devuelve.

**Config nueva (orientativa):**
```yaml
spread_universe:
  min_option_liquidity: 3        # escala 1–5 de tastytrade
  price_range: [20, 500]
etf_index_symbols: [SPY, QQQ, IWM, DIA, XSP, GLD, TLT, XLE, XLF, XLK, SMH, XBI, KRE]
```

**Aceptación:**
- Un ticker sin beneficios pero con liquidez alta, en una fuente apta para spreads, aparece
  en modo Spread y no en Put vendida.
- SPY y XSP no se descartan por falta de EPS ni de datos de EDGAR.

> Nota UE/PRIIPs: una asignación en ETFs estadounidenses puede ser problemática para un
> residente en España. XSP (liquidación en efectivo, estilo europeo) evita el problema.
> Opcional: mostrar un aviso en spreads sobre ETFs de EE. UU.

---

### P2 — Filtro de movimiento esperado (prioridad alta)

**Objetivo:** exigir que el strike vendido quede fuera del rango que descuenta el mercado.

**Cálculo:**
```
EM = precio × IV_vencimiento × sqrt(DTE / 365)
limite_inferior_EM = precio − em_multiplier × EM
filtro: strike_vendido ≤ limite_inferior_EM
```

- `IV_vencimiento`: de `option-expiration-implied-volatilities` de market metrics (misma
  petición que el IV Rank; no hay llamada nueva). Si falta ese vencimiento, interpolar entre
  los vencimientos vecinos o usar la IV30 como último recurso.
- Guardar la IV por vencimiento si hoy no se persiste (migración de añadir).
- Mostrar en la tabla la distancia del strike al límite (en $ y en número de EM).

**Config:** `em_filter_enabled: true`, `em_multiplier: 1.0`.

**Aceptación:** precio 100, IV 0,40, DTE 35 → EM ≈ 12,4; un strike de 90 se descarta y uno
de 87 pasa.

---

### P3 — Condición de volatilidad con «o» (prioridad media)

**Objetivo:** que un pico antiguo de IV no descarte candidatos con IV realmente alta.

**Qué hacer:** añadir un modo de combinación para IV Rank e IV Percentile:
`iv_rank_pct_mode: any | all`. Con `any` basta con que se cumpla uno de los dos.
Por defecto `any` en el perfil Tastytrade.

**Aceptación:** IV Rank 0,25 e IV Pct 0,80 pasa con `any` y no pasa con `all`.

---

### P4 — Precio y selección de spreads (prioridad alta)

**4a. Crédito realista del combo.**
Para spreads, el crédito de referencia es el precio natural del combo:
`bid(put vendida) − ask(put comprada)`. La regla «bid + X % del spread» se aplica sobre el
spread bid/ask del **combo**, no de cada pata por separado:
```
combo_bid = bid_vendida − ask_comprada
combo_ask = ask_vendida − bid_comprada
credito = combo_bid + X% × (combo_ask − combo_bid)
```
Si hoy se usa el mid de cada pata, el crédito sale inflado.

**4b. Filtro de bid/ask del combo sobre el crédito.**
`(combo_ask − combo_bid) / credito ≤ max_combo_spread_pct_credit` (por defecto 0,15).

**4c. Valores por defecto del modo Spread.**
`min_return_on_risk: 0.50` (equivale a crédito = 1/3 de la anchura); permitir bajar a 0,33
(crédito = 25 % de la anchura). Relación: `ROR = C / (W − C)`.

**4d. Una anchura por strike vendido (opcional, conmutable).**
Con `spread_best_width_only: true`, mostrar para cada put vendida solo la **mayor anchura**
que cumple el límite de pérdida máxima en % del NLV y el resto de filtros. Reduce las filas
y encaja con una cuenta pequeña.

**Aceptación:**
- Vendida 2,00/2,10, comprada 1,00/1,10 → combo 0,90/1,10; con X = 0 el crédito es 0,90,
  no 1,00.
- Con `spread_best_width_only`, el mismo strike vendido no aparece repetido.

---

### P5 — Resultados activados en el perfil Tastytrade (prioridad media)

Activar «evitar vencimientos con resultados» por defecto en el perfil Tastytrade, al menos
en los modos Spread y Ambos. Un hueco de precio tras los resultados lleva un spread
directamente a la pérdida máxima. Sigue siendo desactivable en el formulario.

---

### P6 — Gestión de spreads en el Panel (prioridad media)

Hoy el Panel gestiona puts con los perfiles Tastytrade o TPC. Para los spreads, detectar el
par (put vendida + put comprada del mismo subyacente y vencimiento) a partir de las
posiciones y añadir estos avisos:

| Aviso | Condición | Config |
|---|---|---|
| Recoger beneficio | Valor del spread ≤ 50 % del crédito | Existente (50 %) |
| Revisar | DTE ≤ 21 | Existente |
| **Stop** | Valor del spread ≥ 2 × crédito (pérdida ≈ 1 × crédito) | `spread_stop_value_multiple: 2.0` |
| **Riesgo de pin** | DTE ≤ `pin_dte` y precio ≤ strike vendido × (1 + `pin_buffer_pct`) | `pin_dte: 5`, `pin_buffer_pct: 0.02` |
| **Strike vendido en el dinero** | Precio < strike vendido | — |

El crédito sale del `averageCost` de ambas patas (dato de TWS que ya se lee).
Todo son avisos, nunca órdenes.

---

### P7 — Riesgo agregado y correlación (prioridad media)

1. En el Panel, mostrar el **riesgo total abierto** como suma de pérdidas máximas de spreads
   + capital de asignación de puts desnudas, en % del NLV, con aviso si supera
   `max_total_risk_pct` (por defecto 0,50). Mantener el margen what-if como dato aparte.
2. **Grupos de correlación** en config. Aviso si hay más de `max_positions_per_group`
   posiciones abiertas en el mismo grupo:
```yaml
correlation_groups:
  sp500: [SPY, XSP, SPX, VOO, IVV]
  nasdaq: [QQQ, QQQM, NDX]
  small_caps: [IWM]
max_positions_per_group: 1
max_total_risk_pct: 0.50
```

---

### P8 — Mejoras opcionales (prioridad baja)

- **Régimen del VIX como regla:** con VIX > 35, aviso para reducir tamaño y preferir spreads;
  con VIX < 14, aviso de primas pobres. Hoy es solo un aviso de mercado.
- **Skew de puts:** IV de la put de delta ~−0,25 / IV ATM, si la IV por contrato ya se guarda
  en los snapshots. Columna informativa, no filtro.
- **Bear call spreads:** requieren cadenas de calls (más datos de tastytrade). Solo si se
  quiere un sesgo neutral. Señales: precio en resistencia o banda superior de Bollinger,
  RSI > 70 girando.

---

## 3. Valores por defecto sugeridos para el perfil Tastytrade

```yaml
tastytrade_profile:
  dte: [30, 45]
  delta_naked: [0.20, 0.30]        # |delta|
  delta_spread_short: [0.20, 0.35]
  iv_rank_min: 0.30
  iv_pct_min: 0.50
  iv_rank_pct_mode: any
  iv_hv30_min: 1.0
  iv_5d_change_max: 0.0
  entry_signals_min: 1             # «al menos N»
  atr_min_multiple: 2.0
  em_filter_enabled: true
  em_multiplier: 1.0
  avoid_earnings: true
  take_profit_pct: 0.50
  manage_at_dte: 21

spread_mode:
  widths: [1, 2.5, 5, 10]          # para cuenta pequeña, sin 15 y 20
  min_return_on_risk: 0.50
  max_combo_spread_pct_credit: 0.15
  max_loss_pct_nlv: 0.03
  spread_best_width_only: true
  stop_value_multiple: 2.0
  pin_dte: 5
  pin_buffer_pct: 0.02
```

Los nombres de las claves son orientativos: adaptarlos a la estructura real de
`config.yaml` y de `ScanCriteria`.

---

## 4. Orden de implementación recomendado

1. P4a (crédito del combo): afecta a todos los resultados de spreads actuales.
2. P1 (universo para spreads y exención de ETFs/índices).
3. P2 (movimiento esperado).
4. P4b–d, P3 y P5 (ajustes de filtros y valores por defecto).
5. P6 y P7 (Panel).
6. P8 (opcionales).

---

*Información educativa; no constituye asesoramiento financiero.*
