# Selección de tickers (watchlist de tamaño óptimo)

Problema (2026-11-05): 2.351 tickers y ~106.000 contratos cotizados por ciclo; un ciclo completo tardaba ~25 min. Se mide que **los what-if de margen pesan más que
el tamaño de la watchlist** (2026-11-02: el mismo ciclo de ~106.000 contratos tardó 393 s con 3 márgenes pedidos y 1.467 s con 3.594: ~0,3 s cada uno) y que los modos
por ticker solo dejaban de cotizar: el catálogo (puts y calls) se bajaba entero para todos y el modo `cds` lo cumplían ~1.980 tickers.

**Solo queda la fase F0** (ahorro inmediato, sin cambiar la watchlist). Las fases F1 (historial de primas y liquidez de cierre, tabla `ticker_premium_history`), F2 (estados
activo/templado/cuarentena, tabla `ticker_selection`, pantalla «Selección de tickers» de `/scanner/config` y columna «Selección» de la Watchlist) y F3 (barrido de la
cuarentena) se **retiraron el 2026-11-05 a petición del usuario**: se quitó su código, `scanner.selection` y `scanner.closing` (una clave suya en `config.yaml` hace fallar el arranque) y
la migración v40 borra sus dos tablas. Ningún ticker sale ya de la Watchlist por su prima observada; lo único que la reduce es el Universo (filtros de calidad y sincronización).

## F0 – Ahorro inmediato

- **Tope de márgenes por ciclo: retirado (2026-11-05).** Con el margen estimado (`refresh.margin_estimate`) solo las puts vendidas en cartera piden what-if, así que el tope ya no ahorraba nada.
- **Catálogo limitado por los modos** (`scanner.ticker_modes.gate_catalog`, true). `ContractSyncer.modes_provider` (= `AppService.ticker_modes`): un ticker sin `put` ni
  `put_spread` no estrena puts, y uno sin `cds` no estrena calls. **Los contratos ya guardados no se retiran** (salen solos al salir de la ventana): un modo que oscila
  no obliga a cualificar de nuevo. Un ticker sin modos evaluados (nuevo) no tiene límite. Solo actúa con `ticker_modes.enabled`.
- **Recompra fácil en los modos** (los TastyPut, TastySpread y CDS se cierran en días o semanas), con la liquidez de opciones de tastytrade (1–4):
  `put_min_option_liquidity` 2 (puts desnudas; sin dato NO bloquea), `spread_min_option_liquidity` 3 (antes 2) y `cds_min_option_liquidity` 3 (clave nueva; el CDS usaba la de
  los spreads). Un dato que falta apaga spreads y CDS.
- **Calls del CDS con recompra fácil** (`scanner.cds.monthly_only`, true: solo el tercer viernes del mes; `scanner.cds.strike_multiple`, 0: si > 0, strikes múltiplos de N $ a
  partir de 25 $). Medido sobre el catálogo guardado: 34.447 calls, 24.905 en vencimientos mensuales (−28 %) y 22.731 además con strikes múltiplos de 5. A diferencia
  del límite por modos, esto SÍ retira las calls guardadas que no cumplen.

## Criba teórica (informe, 2026-11-05)

`/scanner/criba` (enlace en `/scanner/config`; `scanner/screen.py`, `ui/routes_criba.py`, `tests/unit/test_screen.py`). **Solo informa: no quita nada de las fuentes ni de la Watchlist.**
Con el precio y la IV de cada ficha (la mayor entre `iv30` y la de cualquier vencimiento de tastytrade) calcula con Black-Scholes la mejor prima posible de la ventana de cada perfil de
puts (strike al descuento mínimo, DTE máximo, precio mid, sin comisiones, delta ni demás filtros) y la compara con su yield mínimo. **Descartable** = ni multiplicando esa prima por la
tolerancia (3× por defecto, editable en la página; la prima real llega a ~2,8× la teórica) se alcanza lo exigido en NINGÚN perfil de puts; «dudoso» = solo con la tolerancia; «sin dato» = sin
precio, IV o perfiles. Los tickers con modo spread o CDS encendido se marcan como «mantenidos». Los perfiles de spread y CDS no se evalúan.

Medido el 2026-11-05 sobre 2.353 fichas con los perfiles de puts GUARDADOS (Mensual (TPC), Táctico (TPC) y TastyPut; TastySpread y MTO CDS no se evalúan): **0 tickers descartables**, porque
«Mensual (TPC)» no tiene yield mínimo (0) y, con él, ningún ticker falla todos los perfiles. Por separado sí discrimina: «Táctico (TPC)» (DTE 1–7, yield ≥ 1 %) no lo alcanzan 1.247 tickers
(864 ni con 3×) y TastyPut (descuento ≥ 5 %, DTE 35–50, yield ≥ 1 %) solo 6 (3 ni con 3×). Conclusión: la criba por yield teórico solo recorta si se aplica por perfil (p. ej. no cotizar un ticker
para el perfil que no puede cumplir), no sobre la unión de todos. (Una primera medida con los presets de `config.yaml` y el perfil «Estrategia», que no está entre los perfiles guardados, daba 3–5 descartables y era errónea.)

## Filtro de liquidez en el Universo y paso de velas (2026-11-05)

**Liquidez de opciones en el Universo.** El bloque «Filtros de calidad» del Universo tiene ahora «Liq. opc. ≥ N» (1–4, la valoración de tastytrade; mismos valores permitidos que en el Scanner,
`scanner.quality.liquidity_options`). Un ticker sin dato no pasa y los instrumentos de TWS no se miden. Medido sobre 2.353 fichas: ≥ 2 deja 1.982 (liquidez 1: 368; sin dato: 3), ≥ 3 deja 1.029 y ≥ 4, 447.
Por sí sola predice mal el yield: de 12 tickers de una muestra que nunca llegaron al 1 %, solo 3 tenían liquidez 1.

**Histórico real de bids (informe, no quita nada).** Sección «Histórico real de bids» de `/scanner/criba` (`scanner/yield_history.py`, `AppService.fetch_yield_history/yield_history_report`,
`TastytradeVolatility.option_bid_history`, tabla `option_bid_history` en la migración v42, `BidHistoryRepo`). Para los últimos N terceros viernes ya vencidos (3 por defecto) toma el día en que faltaban 30 días,
el precio de ese día (`daily_bars`) y los strikes de la rejilla habitual (pasos de 0,5/1/2,5 por debajo de 50 $, 1/2,5/5 hasta 200 $, 5/10 por encima) iguales o inferiores al descuento mínimo del perfil elegido;
pide a tastytrade las velas diarias `price=bid` de esos contratos (también vencidos; símbolo dxFeed `.AAPL261016P250`) y guarda el bid de cierre de ese día (o de hasta 3 días antes). El yield de un mes es el mejor
bid / strike de sus strikes. **Descartable** = hay al menos «meses con dato» (2) con bid y menos de «aciertos» (2) llegan al yield mínimo del perfil; con menos meses con dato, «sin dato»; con contratos por pedir, «sin pedir».
**Spread, OI e IV (2026-11-06, migración v43).** Si el perfil elegido exige spread máximo u OI mínimo (`ScanCriteria.max_spread_pct/min_oi`), «Pedir los datos que faltan» pide además, solo de los contratos CON bid, las velas `price=ask` (ask de cierre) y `price=last` (OI, IV y volumen; **solo vienen en las velas `last`, que existen únicamente los días con operaciones**; medido con 12 puts vencidas: OI en 351 de 383 velas) (`TastytradeVolatility.option_detail_history`, `candle_value`; se guardan en `option_bid_history.ask/open_interest/iv/volume`, `detail_at` marca lo ya pedido). Un strike solo cuenta si su spread de cierre (ask y bid, `metrics.spread`) y su OI cumplen los del perfil; un dato que falta no pasa, como en el Scanner (`yield_history.strike_yield`). Un mes con bid pero ningún strike líquido cuenta como mes con dato y 0 % (no «sin dato») y la tabla anota «(−n sin liquidez)»; con filtros y sin ask/OI/IV guardados el strike queda «sin pedir». Sin filtros en el perfil no se piden (dos series más por contrato: el tiempo se triplica) y vale solo el bid. **El tamaño del bid no existe en las velas**, y el spread sale de dos cierres diarios que pueden no ser simultáneos; el OI/IV son de la última vela con operaciones (hasta 3 días antes).
Lo guardado no se vuelve a pedir y lo que tastytrade no contesta se repite en la siguiente petición. Botón «Pedir los datos que faltan» (`POST /scanner/criba/historico`, en segundo plano, tramos de 1.000 símbolos,
lotes de 50 con una conexión por lote y hasta 3 pasadas).

Medidas previas (2026-11-05, 60 tickers al azar, descuento ≥ 5 %, 3 vencimientos): 51 con datos; 12 nunca llegaron al 1 % (6 ni al 0,5 %) y otros 14 solo en un mes de tres; 9 sin ningún bid (puede ser que no tengan
mensuales o un strike fuera de la rejilla). La criba teórica no descartó a ninguno de los 60. Coste medido: 186 símbolos tardaron 72 s con 3 pasadas (la primera casi sin respuestas); unos 6,5 símbolos por ticker,
así que ~1.980 tickers son ~12.900 símbolos (del orden de 80 minutos, sin optimizar). Límites: bid de cierre (spread y OI solo si el perfil los exige, ver arriba), sin tamaño; 3 meses son pocos y un mes con resultados distorsiona; las clases de acciones no se consultan.
