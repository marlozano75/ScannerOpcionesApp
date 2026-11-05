# Selección de tickers (watchlist de tamaño óptimo)

Problema (2026-11-05): 2.351 tickers y ~106.000 contratos cotizados por ciclo; un ciclo completo tardaba ~25 min. Se mide que **los what-if de margen pesan más que
el tamaño de la watchlist** (2026-11-02: el mismo ciclo de ~106.000 contratos tardó 393 s con 3 márgenes pedidos y 1.467 s con 3.594: ~0,3 s cada uno) y que los modos
por ticker solo dejaban de cotizar: el catálogo (puts y calls) se bajaba entero para todos y el modo `cds` lo cumplían ~1.980 tickers.

**No es Admisión.** Admisión (retirada el 2026-10-26) filtraba con reglas fundamentales estáticas por fuente. Esto usa **primas y liquidez observadas día a día**, con
histéresis, y recorta lo que se guarda y se cotiza; las fuentes solo dirán a qué perfiles apuntan (F2), no llevan filtros propios.

Plan por fases (acordado con el usuario el 2026-11-05). **Hechas: F0, F1 y F2** (F2 apagada por defecto). Pendiente: F3.

## F0 – Ahorro inmediato (sin cambiar la watchlist)

- **Tope de márgenes por ciclo** (`refresh.margin_max_per_cycle`, 800; 0 = sin tope). Dentro de cada lote se piden primero los contratos de mayor yield anualizado
  (`RefreshJob.run`); los que quedan sin margen se piden en los ciclos siguientes (los ya pedidos se reutilizan). El log del refresco añade «aplazados N».
- **Catálogo limitado por los modos** (`scanner.ticker_modes.gate_catalog`, true). `ContractSyncer.modes_provider` (= `AppService.ticker_modes`): un ticker sin `put` ni
  `put_spread` no estrena puts, y uno sin `cds` no estrena calls. **Los contratos ya guardados no se retiran** (salen solos al salir de la ventana): un modo que oscila
  no obliga a cualificar de nuevo. Un ticker sin modos evaluados (nuevo) no tiene límite. Solo actúa con `ticker_modes.enabled`.
- **Recompra fácil en los modos** (los TastyPut, TastySpread y CDS se cierran en días o semanas), con la liquidez de opciones de tastytrade (1–4):
  `put_min_option_liquidity` 2 (puts desnudas; sin dato NO bloquea), `spread_min_option_liquidity` 3 (antes 2) y `cds_min_option_liquidity` 3 (clave nueva; el CDS usaba la de
  los spreads). Un dato que falta apaga spreads y CDS.
- **Calls del CDS con recompra fácil** (`scanner.cds.monthly_only`, true: solo el tercer viernes del mes; `scanner.cds.strike_multiple`, 0: si > 0, strikes múltiplos de N $ a
  partir de 25 $). Medido sobre el catálogo guardado: 34.447 calls, 24.905 en vencimientos mensuales (−28 %) y 22.731 además con strikes múltiplos de 5. A diferencia
  del límite por modos, esto SÍ retira las calls guardadas que no cumplen.

## F1 – Historial de primas y liquidez de cierre

Tabla `ticker_premium_history` (migración v38; `PremiumRepo`, `scanner/premium.py`, `RefreshJob._record_premium`): una fila por **ticker, perfil y día**. Se escribe al final de
cada refresco **completo** de mercado (no en los parciales ni en `only_unquoted`); la última medida del día gana. El día es el de la sesión (`MarketCalendar.session_day` o,
con el mercado cerrado, el del último cierre): tras el cierre el refresco conserva la última cotización válida, así que la medida es la de la sesión y no la de un bid 0 con
ask inflado. Solo hay filas de los tickers que se cotizaron (los que los modos dejan fuera no tienen medida: ausencia ≠ cero). Perfiles: los guardados con instrumento put
(`AppService._premium_profiles`); sin ellos, «Inicial» (`scanner.initial`) y «Estrategia». Con `scanner.cds.enabled` hay además una fila «CDS» (calls de su ventana).

| Columna | Qué es |
|---|---|
| `contracts` | contratos con bid dentro de la ventana del perfil (descuento mínimo/máximo y DTE) |
| `closable` | de ellos, los **cerrables**: bid > 0, spread bid/ask ≤ `scanner.closing.max_spread_pct` (20) y OI ≥ `scanner.closing.min_oi` (50) |
| `best_yield_pct` | mejor yield del periodo en la ventana, con el precio de referencia del perfil |
| `best_closable_yield_pct` | el mejor entre los cerrables (la prima que de verdad se puede cobrar y recomprar) |
| `best_ann_wide_pct` | mejor yield **anualizado** con el mismo descuento mínimo y DTE 1…`closing.wide_dte_max` (60): no depende de qué vencimientos caen hoy en la ventana |
| `median_spread_pct`, `max_oi` | liquidez de la ventana |

Por qué la banda ancha: hoy 25–35 DTE solo contiene semanales y 1.586 tickers no tienen ningún contrato ahí; una foto de la ventana estrecha depende del calendario de
vencimientos. La decisión de F2 se tomará acumulando días (N de M), nunca con una sola foto. Una estimación teórica con Black-Scholes y la IV a 30 días se desvía 0,18×–2,8×
de la prima real según el ticker: sirve para descartar extremos con holgura, no para decidir un ticker. `scanner.closing.keep_days` (180) poda lo antiguo; las escrituras no
invalidan las cachés del Scanner. Un contrato «cerrable» es una medida de hoy: no garantiza poder salir tras un movimiento fuerte del subyacente.

## F2 – Tres estados por ticker (hecha 2026-11-05, apagada: `scanner.selection.enabled`)

`scanner/selection.py` (funciones puras), `SelectionRepo` / tabla `ticker_selection` (migración v39), `AppService.evaluate_selection/ticker_selection/quarantined_tickers/warm_tickers`.
Se evalúa tras cada refresco completo de mercado (`refresh_all`).

**Medida diaria (`day_ratio`).** Para cada sesión, cuánto de lo exigido cumple el ticker en su mejor perfil permitido (1 = cumple con un contrato **cerrable**):
mejor yield de contratos cerrables / yield mínimo del perfil; si no hay cerrables, el mejor yield × 0,8 (una prima que no se puede recomprar nunca es un acierto: como mucho
templado); si no hay contratos en la ventana (calendario de vencimientos), el yield anualizado de la banda ancha frente al exigido × 0,8; CDS: 1 con calls cerrables, 0,5 con calls
pero ninguna cerrable. Sin dato utilizable la sesión no cuenta.

**Estado (`evaluate`)**, con las últimas `window_days` (10) sesiones con medida y **nunca con menos de `min_days` (5): antes, ACTIVO**:
- **ACTIVO**: ratio ≥ 1 en ≥ `min_hits` (3) sesiones (uno menos si ya era activo: histéresis).
- **TEMPLADO**: no llega a activo pero ratio ≥ `warm_ratio` (0,6) en ≥ `min_hits` sesiones. Sigue en la Watchlist y **se cotiza una vez al día** (`warm_quote_once_a_day`;
  `RefreshJob.warm_tickers`: se salta sus contratos si ya tienen un snapshot de hoy; el log añade «templados: N contratos ya cotizados hoy»). Solo se exceptúan los refrescos de
  tickers concretos (`run(tickers=…)`).
- **CUARENTENA**: lo demás. `watchlist_sync_plan` la excluye de lo deseado y `apply_watchlist_sync` la quita **sin esperar los `grace_days`** (como los PTP: ya se evaluó durante
  varias sesiones). Solo actúa con la sincronización con el Universo en «on»; con la Watchlist a mano la cuarentena se anota pero no quita nada. La primera pasada que quite
  más de 30 y más del 25 % pasa por el banner de confirmación existente (`wl_sync_held`).
- **Salida de cuarentena**: a los `quarantine_days` (14) el ticker vuelve a ser ACTIVO con `released_at`; solo cuentan las medidas **posteriores**, así que hasta que haya `min_days`
  sesiones nuevas no se vuelve a descartar. (Sin F3 un ticker en cuarentena no se cotiza, de ahí esta caducidad.)

**Exentos** (siempre ACTIVO, motivo «exento»): tickers con posiciones abiertas, instrumentos propios y fuentes de contratos (`Source.kind == "contracts"`: Comunidad, MarketRadar).

**Fuentes → perfiles** (`selection.source_profiles`): `{fuente: [perfiles]}`. Los perfiles que cuentan para un ticker son la unión de los de sus fuentes; una fuente sin entrada
(o con lista vacía) vale para todos los perfiles. «CDS» es el perfil de las calls del CDS. No hay filtros nuevos por fuente: solo a qué perfiles apunta cada una. Aún no se usa para
reducir la ventana del catálogo de cada ticker (la unión de las ventanas de sus perfiles): queda para después, cuando haya historial con que validarlo.

**UI**: columna «Selección» en la Watchlist (con el motivo y desde cuándo en el tooltip) cuando está activada.

**Configuración desde la aplicación (2026-11-05)**: sección «Selección de tickers» de `/scanner/config` (`ui/selection_form.py`, `POST /scanner/selection`, `AppService.selection_config/
save_selection_config/reset_selection_config/selection_preview`). Edita: activar/desactivar, `selection.*` (ventana, sesiones mínimas, aciertos, % de templado, días de cuarentena,
templados una vez al día, tabla fuente → perfiles), la liquidez mínima por modo (`ticker_modes.put/spread/cds_min_option_liquidity`), los criterios de «cerrable»
(`closing.max_spread_pct/min_oi`) y `refresh.margin_max_per_cycle`. Lo guardado va a `meta.selection_user_config`, **manda sobre config.yaml** hasta «Restaurar los de config.yaml»
y se aplica al momento (los ajustes son `frozen` y compartidos: se escriben en el objeto, como los presets del Scanner). Siguen solo en config.yaml: `ticker_modes.enabled` y el resto
de `ticker_modes.*`, `closing.enabled/wide_dte_max/keep_days`, `cds.monthly_only/strike_multiple`, `gate_catalog`.
- **Vista previa** (`selection_preview`, no guarda ni aplica nada): recuento activos/templados/cuarentena, exentos, tickers que siguen activos por falta de historial, sesiones con
  medida frente a las necesarias, cuántos saldrían de la Watchlist (y si superaría el límite de seguridad de una pasada automática), listas con el motivo de cada uno y, con los modos
  por ticker activos, cuántos tickers tendrían cada modo antes y después de la nueva liquidez. Usa la histéresis y las liberaciones reales.
- **Guardar** aplica y reevalúa. Si el resultado saca tickers de la Watchlist (selección activada, sincronización «on» y alguno en cuarentena), la primera pulsación solo muestra la vista
  previa con «Confirmar y guardar»: no se puede aplicar sin haberla visto. Un valor inválido (p. ej. más aciertos que sesiones de ventana) se avisa y no guarda nada.
- Cambiar `closing.*` solo afecta a las medidas que se registren a partir de ese momento (el historial guarda ya el recuento de cerrables); la liquidez de los modos se aplica en la
  siguiente evaluación de modos (cada refresco).

**Antes de activarla**: deja correr F1 unas sesiones (hace falta historial; la app lo registra sola) y revisa la columna «Selección» con `enabled: true` y
`quarantine_days` alto antes de dejar que quite tickers. Los umbrales (`min_hits`, `warm_ratio`, `closing.*`) son de partida y se ajustarán con las primas reales.

## F3 – Barrido de cuarentena (pendiente)

Re-evaluación barata de los tickers en cuarentena en días sin operar, cotizando unos pocos contratos de prueba por ventana de perfil en vez del catálogo. Claves previstas:
`selection.sweep_days` y `selection.probe_contracts`.
