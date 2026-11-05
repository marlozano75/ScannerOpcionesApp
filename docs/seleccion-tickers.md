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
