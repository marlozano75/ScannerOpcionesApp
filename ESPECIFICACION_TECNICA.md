# Especificación técnica — ScannerOpcionesApp

> Documento destinado a un agente de IA implementador. Deriva **exclusivamente** de `especificaciones.md`.
> **Estado: LISTO PARA IMPLEMENTAR.** Todas las preguntas están resueltas o aceptadas con su valor provisional (marcado "ACEPTADO"); el usuario puede cambiarlos vía configuración.
> Convenciones:
> - **[REQ]** = requisito explícito en la especificación original.
> - **[PROPUESTA]** = decisión técnica propuesta por el analista (no viene del usuario; modificable).
> - **PENDIENTE (Q-xx)** = información que falta. El implementador **NO debe decidirla por su cuenta**: debe usar el valor por defecto indicado (si existe), dejarlo configurable y marcar el punto con `# TODO(Q-xx)`.

---

## 1. Resumen y alcance

Aplicación de escritorio/local que, conectada a TWS de Interactive Brokers (cuenta de margen Reg T, Irlanda; real o simulada), ofrece:

1. **Scanner de opciones** sobre una watchlist (operaciones "Regular" y "Táctica" + filtros).
2. **Panel de riesgo** por apalancamiento (Cushion, semáforo, Look Ahead / Post-Expiration / Severidad IBKR, VIX).
3. **Panel de diversificación** sectorial (actual y próximas 5 semanas).
4. **Simulador** de cartera si se ejercen contratos seleccionados.

**Decisiones confirmadas (Q-01):** el scanner es **solo para venta de puts**; la aplicación es de solo lectura y **nunca envía órdenes**. Solo usa órdenes *what-if* para calcular margen.

---

## 2. Requisitos funcionales

| ID | Requisito | Origen |
|----|-----------|--------|
| RF-01 | Conectar a TWS vía API. Selector cuenta **real** / **simulada** (puertos y clientId configurables). | L3 |
| RF-02 | Introducir watchlist pegando tickers (texto libre). | L10 |
| RF-03 | Introducir watchlist cargando archivo Excel (.xlsx) o texto (.txt/.csv). | L10 |
| RF-04 | Actualización **diaria** por ticker: sector, categoría, expiraciones, DTE, strikes, contratos candidatos, días hasta ex-dividendo (si aplica), historial IV 1 año → IV Rank e IV Percentile. | L12-20 |
| RF-05 | Los tickers añadidos **después** de la ejecución diaria se actualizan (bloque diario) en el momento de añadirse. | L12 |
| RF-06 | El historial de IV se **persiste** y en actualizaciones posteriores solo se descargan las entradas no guardadas (optimización). | L20 |
| RF-07 | Cada **X minutos** (configurable) refrescar por contrato: Bid, Ask, Delta, IV, Last, OI, timestamp de última actualización; calcular Spread %, Yield, Yield anualizado, IV Rank, IV Percentile, margen inicial si se ejecuta. | L21-34 |
| RF-08 | Scanner **Regular** (puts vendidas): descuento mínimo del strike respecto al precio (inicial **20 %**), yield bruto mínimo (inicial 1 %), DTE mín. y máx. (inicial **25 y 35**). Todo editable en el formulario. | L6 + decisión del usuario 2026-09-29 |
| RF-09 | Scanner **Táctico**: descuento mínimo del strike (inicial **10 %**), yield bruto mínimo (inicial 1 %) y **solo DTE máx.** (inicial **15**; el mínimo es 1 y no se muestra). Todo editable. | L7 + decisión del usuario 2026-09-29 |
| RF-10 | Filtros adicionales: OI mínimo, Spread máximo, IV Rank, IV Percentile. | L8 |
| RF-11 | Al ejecutar el escaneo se muestran **solo** contratos que cumplen los criterios. | L36 |
| RF-12 | Cada contrato mostrado incluye: incremento del peso de su sector en la cartera si se toma; peso respecto al resto de contratos que expiran la misma semana. | L38 |
| RF-13 | Cada contrato incluye el % de cartera que supondría si es asignado al precio de strike. | L39 |
| RF-14 | Seleccionar varios contratos y simular diversificación y riesgo (cushion, márgenes, …). Mostrar cartera actual vs. futura si se ejercieran. | L42 |
| RF-15 | Panel de riesgo: Cushion de cartera + semáforo. | L44-48 |
| RF-16 | Mostrar Look Ahead y Post-Expiration con el semáforo de cushion, y la Severidad (`HighestSeverity` de IBKR) en lugar de Overnight (0 verde, 1 ámbar, 2 naranja, 3 rojo). | L50 + decisión del usuario |
| RF-17 | Mostrar VIX últimos 5 días, VIX actual y futuros VIX previstos a 2-3 semanas. | L52 |
| RF-18 | Panel de diversificación: en cada ejecución actualizar sectores y % de cartera por sector. | L56 |
| RF-19 | Diversificación sectorial para las próximas 5 semanas con contratos abiertos. | L57 |
| RF-20 | Panel de riesgo: mostrar **Gross Position Value** (tag `GrossPositionValue` de IBKR), **Nominal Assignment Exposure** = Short Put Exposure − Long Put Protection (nominal = strike × multiplicador × contratos) y **Leverage Assignment** = NAE / NLV. También antes/después en el simulador. | Petición del usuario 2026-09-29 |
| RF-21 | VIX y futuros VIX solo con barras históricas diarias (sin suscripción en tiempo real; futuros CFE con `useRTH=False`). Ningún paso de red puede colgarse: timeouts. | Petición del usuario 2026-09-29 |
| RF-23 | La actualización diaria guarda los contratos con strike de −10 % a −45 % y DTE hasta 60 días (configurable en `scanner.candidates`). Cada ciclo automático solo cotiza los que encajan con los valores iniciales de Regular/Táctica; un botón cotiza el rango elegido en el formulario. | Petición del usuario 2026-09-29 |
| RF-24 | Al quitar un ticker de la watchlist se borran sus contratos (con sus cotizaciones) y su ficha; el historial de IV se conserva. Al arrancar y antes de cada refresco se eliminan los datos de tickers que ya no están en la watchlist. | Petición del usuario 2026-09-29 |
| RF-25 | La actualización diaria forzada se ejecuta en segundo plano y espera su turno si hay otra tarea en curso (no se omite en silencio); la interfaz muestra la tarea en curso y su progreso. | Petición del usuario 2026-09-29 |
| RF-26 | El precio del subyacente se actualiza en cada ciclo de refresco (no solo en la actualización diaria); las distancias y el filtro de descuento usan ese precio. La columna se llama **Desc.** | Petición del usuario 2026-09-29 |
| RF-27 | Por contrato se trae y muestra también el **Bid size** (tamaño del bid). | Petición del usuario 2026-09-29 |
| RF-22 | En el scanner, OI mín., spread máx., IV Rank mín. e IV Percentile mín. son **opcionales**: se aplican solo si el usuario marca su casilla. El descuento del strike (mín./máx.) y el yield bruto mínimo son editables en el formulario (valores iniciales de la configuración). | Petición del usuario 2026-09-29 |

## 3. Requisitos técnicos / no funcionales

| ID | Requisito |
|----|-----------|
| RT-01 | Fuente única de datos: API de TWS (o IB Gateway). No se usan otras fuentes salvo aprobación (ver Q-12). |
| RT-02 | Respetar límites de la API IBKR: líneas de market data simultáneas, pacing de históricos (≈60 peticiones/10 min), límites de what-if. Toda petición pasa por un limitador central. |
| RT-03 | Toda la configuración (puertos, X minutos, umbrales, semáforos, rangos DTE) en fichero externo; nada "hardcodeado". |
| RT-04 | Manejo de errores explícito: TWS desconectado, ticker inválido, sin suscripción de datos, sin datos (bid/ask = -1/NaN), timeouts. La app **degrada** (marca el dato como no disponible) sin caerse. |
| RT-05 | Persistencia local (SQLite [PROPUESTA]) para: watchlist, metadatos, historial IV, último snapshot de contratos. |
| RT-06 | Núcleo de negocio **independiente** de IBKR y de la UI (puertos/adaptadores) para poder testear con datos simulados. |
| RT-07 | Logging estructurado con niveles; los errores de TWS (códigos) se registran. |
| RT-08 | Cada valor mostrado indica su antigüedad (timestamp) [REQ para contratos; PROPUESTA para el resto]. |
| RT-09 | Ejecución en Windows 11 [entorno actual del usuario]. |

---

## 4. Módulos: entradas, proceso, salidas

### M1 — Configuración (`config`)
- **Entrada:** `config/config.yaml`, variables de entorno (`.env`).
- **Proceso:** carga y valida con esquema tipado; expone objeto inmutable.
- **Salida:** `Settings`. Error claro si falta/erróneo algún campo.

### M2 — Broker / Conexión IBKR (`broker`)
- **Entrada:** modo (real/simulada), host, puerto, clientId.
- **Proceso:** conexión, reconexión, limitador de peticiones; traducción de tipos IBKR → modelos de dominio. Interfaz `BrokerGateway` (protocolo) con implementaciones `IBKRGateway` y `FakeGateway` (tests).
- **Salida:** métodos: `get_account_summary()`, `get_positions()`, `get_contract_details(ticker)`, `get_option_chain(ticker)`, `get_quotes(contracts)`, `get_iv_history(ticker, desde)`, `get_underlying_price(ticker)`, `get_dividend_info(ticker)`, `what_if_margin(contract, qty)`, `get_vix_data()`.

### M3 — Persistencia (`storage`)
- **Entrada/Salida:** repositorios: `WatchlistRepo`, `TickerInfoRepo`, `IVHistoryRepo`, `ContractRepo`, `SnapshotRepo`.
- **Proceso:** SQLite con migraciones simples; solo capa de acceso a datos.

### M4 — Watchlist (`watchlist`)
- **Entrada:** texto pegado, o archivo .txt/.csv/.xlsx.
- **Proceso:** parseo (separadores coma, espacio, salto de línea, tabulador), normalización (mayúsculas), deduplicado, validación de formato.
- **Salida:** lista de tickers válidos + lista de rechazados con motivo. Marca como "pendientes de actualización diaria" los nuevos.

### M5 — Actualización diaria (`jobs/daily`)
- **Entrada:** watchlist.
- **Proceso (por ticker):** sector/categoría → expiraciones y DTE → strikes → contratos candidatos → días a ex-dividendo → IV histórica incremental → IV Rank / IV Percentile.
- **Salida:** `TickerInfo`, `OptionContract` (candidatos), `IVHistory`; informe de errores por ticker.

### M6 — Refresco periódico (`jobs/refresh`)
- **Entrada:** contratos candidatos, X minutos.
- **Proceso:** obtener cotizaciones y griegas; calcular métricas (M7); solicitar margen what-if.
- **Salida:** `ContractSnapshot` con `updated_at`.

### M7 — Métricas (`metrics`, funciones puras)
- Spread %, Yield, Yield anualizado, IV Rank, IV Percentile (fórmulas en §8).
- **Entrada:** números; **Salida:** números o `None` si no computable.

### M8 — Scanner (`scanner`)
- **Entrada:** snapshots + `ScanCriteria` (tipo Regular/Táctica, % strike, yield mín., DTE min/max, OI mín., spread máx., IV Rank mín., IV Percentile mín.).
- **Proceso:** filtrado con predicados componibles; enriquecimiento con M10 (peso sector, % si asignación).
- **Salida:** lista ordenable de `ScanResult`, más un motivo de descarte disponible en modo debug.

### M9 — Cartera y riesgo (`portfolio`)
- **Entrada:** resumen de cuenta, posiciones.
- **Proceso:** cushion y semáforo; Look Ahead / Post-Expiration; severidad.
- **Salida:** `RiskStatus` (valores + nivel de semáforo).

### M10 — Diversificación (`portfolio/diversification`)
- **Entrada:** posiciones (acciones + opciones abiertas), mapa ticker→sector.
- **Proceso:** % por sector actual; % por sector por semana de expiración (5 semanas); incremento por contrato candidato.
- **Salida:** `SectorExposure` actual y `WeeklyExposure[5]`.

### M11 — Simulador (`portfolio/simulator`)
- **Entrada:** cartera actual + lista de contratos seleccionados (con cantidad).
- **Proceso:** cartera hipotética asumiendo asignación de todos al strike; recalcula pesos y riesgo.
- **Salida:** `SimulationResult` (actual vs. futura, delta de sectores, delta de margen/cushion).

### M12 — Mercado VIX (`market`)
- **Entrada:** conexión broker.
- **Salida:** VIX actual, últimos 5 cierres, curva de futuros VIX (2-3 vencimientos).

### M13 — Planificador (`scheduler`)
- Lanza M5 (diario y bajo demanda para tickers nuevos) y M6/M9/M12 cada X minutos; evita ejecuciones solapadas.

### M14 — Interfaz (`ui`)
- Pestañas: Conexión/Watchlist, Scanner, Simulador, Riesgo, Diversificación. Sin lógica de negocio.

---

## 5. Arquitectura propuesta [PROPUESTA]

Arquitectura hexagonal ligera:

```
UI  ──►  Servicios de aplicación  ──►  Dominio (métricas, scanner, portfolio)
                    │
                    ├──► BrokerGateway (puerto) ◄── IBKRGateway / FakeGateway
                    └──► Repositorios (puerto)  ◄── SQLite
```

- **Lenguaje:** Python ≥ 3.11. **Cliente IBKR:** `ib_async` (sucesor mantenido de `ib_insync`) [PROPUESTA].
- **Validación/config:** `pydantic` + YAML. **Datos:** `pandas` solo en bordes (Excel, tablas UI); el dominio usa `dataclasses`.
- **UI:** (Q-02 aceptada). aplicación local con **FastAPI + Jinja2/HTMX** (evita los problemas de bucle asyncio de Streamlit con TWS). El núcleo no depende de la elección.
- **Concurrencia:** un único bucle asyncio propietario de la conexión IBKR; los jobs se ejecutan en él.
- **Reglas:** el dominio no importa `ib_async`; toda interacción con IBKR pasa por `BrokerGateway`; funciones de métricas puras.

## 6. Estructura de carpetas

```
ScannerOpcionesApp/
├─ README.md
├─ ESPECIFICACION_TECNICA.md
├─ pyproject.toml
├─ .env.example
├─ config/
│  ├─ config.example.yaml
│  └─ config.yaml                # ignorado por git
├─ data/                         # SQLite, logs (ignorado por git)
├─ src/scanner_opciones/
│  ├─ __init__.py
│  ├─ main.py                    # arranque / composición de dependencias
│  ├─ config/        settings.py
│  ├─ domain/        models.py  enums.py  errors.py
│  ├─ broker/        base.py  ibkr_gateway.py  ibkr_mapper.py  rate_limiter.py  fake_gateway.py
│  ├─ storage/       db.py  migrations.py  repositories.py
│  ├─ watchlist/     parser.py  loader.py  service.py
│  ├─ metrics/       spread.py  yields.py  iv_stats.py
│  ├─ scanner/       criteria.py  filters.py  engine.py  enrichment.py
│  ├─ portfolio/     risk.py  cushion.py  diversification.py  simulator.py
│  ├─ market/        vix.py
│  ├─ jobs/          daily_update.py  refresh.py  scheduler.py
│  └─ ui/            (según Q-02)
└─ tests/
   ├─ unit/          metrics, filters, parser, cushion, diversification, simulator, settings
   ├─ integration/   storage (SQLite en memoria), jobs con FakeGateway
   ├─ fixtures/      cadenas de opciones, historiales IV, watchlists de ejemplo
   └─ manual/        test_ibkr_smoke.py (requiere TWS, excluido de CI)
```

## 7. Configuración externa (`config.yaml`)

| Clave | Ejemplo / defecto | Notas |
|-------|-------------------|-------|
| `ibkr.host` | `127.0.0.1` | |
| `ibkr.ports.live` / `paper` | `7496` / `7497` | Puertos habituales TWS; Gateway usa 4001/4002 |
| `ibkr.client_id` | `1` | |
| `ibkr.mode` | `paper` | Por defecto **simulada** por seguridad [PROPUESTA] |
| `ibkr.market_data_type` | `1` (live) para opciones; VIX/futuros VIX admiten datos diferidos (`3`) | Q-11 resuelta |
| `refresh.interval_minutes` (X) | `5` | Q-08 resuelta |
| `daily_update.run_on_startup` | `true` | Al arrancar la app se ejecuta la actualización diaria si aún no se hizo hoy. **Sin hora fija** (Q-09) |
| `scanner.candidates.*` | strikes `10`–`45` % por debajo, DTE `1`–`60` | **Rango que se GUARDA** en la actualización diaria; el scanner solo ve contratos dentro de él |
| `scanner.regular.strike_below_pct` | `20` | descuento **mínimo** del strike; valor inicial editable |
| `scanner.regular.min_yield_pct` | `1.0` | |
| `scanner.regular.dte_min` / `dte_max` | `25` / `35` | configurable (Q-03) |
| `scanner.tactical.strike_below_pct` | `10` | descuento **mínimo**; valor inicial editable |
| `scanner.tactical.min_yield_pct` | `1.0` | |
| `scanner.tactical.dte_min` / `dte_max` | `1` / `15` | en el formulario solo se edita el máximo |
| `scanner.filters.min_oi` / `max_spread_pct` / `min_iv_rank` / `min_iv_percentile` | sin valor (filtro desactivado) | |
| `risk.cushion_thresholds` | `normal_above: 40`, `concern_above: 30` | verde >40 / ámbar 30-40 / rojo <30 (Q-05) |
| `diversification.weeks_ahead` | `5` | |
| `vix.history_days` / `vix.futures_ahead` | `5` / `3` | |
| `iv.lookback_days` | `365` | |
| `storage.path` | `data/app.db` | |
| `logging.level` | `INFO` | |

## 8. Fórmulas [PROPUESTA salvo indicación; validar con Q-06/Q-07]

- **Spread %** = `(ask − bid) / mid × 100`, con `mid = (ask+bid)/2`. (Q-06: ¿sobre mid o sobre ask?)
- **Yield (gross premium yield)** [REQ: "prima por acción dividido entre el precio del strike"] = `prima / strike`. Prima = `mid = (bid+ask)/2` [Q-06 resuelta].
- **Yield anualizado** = `yield × 365 / DTE` (lineal, no compuesto). (Q-06)
- **IV Rank** = `(IV_actual − IV_min_252d) / (IV_max_252d − IV_min_252d) × 100`.
- **IV Percentile** = `% de días de la ventana con IV < IV_actual`.
- **Cushion** = `ExcessLiquidity / NetLiquidation` (definición IBKR; se puede leer directamente el tag `Cushion` del account summary).
- **Cushion actual** = el tag `Cushion` de IBKR (fracción ×100), sin recalcular. **Cushion Look Ahead / Post-Expiration** [Q-05b] = `Excess Liquidity del escenario / NetLiquidation`.
- **Exposición potencial total (EPT)** [Q-10] = `Σ valor de mercado de las acciones` + `Σ exposición nominal de puts vendidas` (nominal = `strike × multiplicador × nº contratos`). Es la **base de todos los pesos**.
- **Peso sector** = `(valor acciones del sector + nominal puts vendidas del sector) / EPT`.
- **% cartera si asignación** = `nominal del contrato candidato / (EPT + nominal del candidato)` (el candidato se suma a la EPT al ser una nueva exposición).
- **Incremento peso sector** = `(exp_sector + nominal_candidato) / (EPT + nominal_candidato) − exp_sector / EPT`.
- **Peso respecto a la misma semana** [Q-10b] = `nominal del candidato / (nominal total de puts vendidas abiertas con expiración en esa semana + nominal del candidato)`. Semana = semana natural (lunes-domingo) de la fecha de expiración.
- **Distancia strike** = `(precio − strike) / precio × 100`.
- **DTE** = días naturales hasta expiración.

## 9. Modelos de dominio (campos mínimos)

- `TickerInfo`: ticker, sector, categoría, precio subyacente, días a ex-dividendo (nullable), iv_rank, iv_percentile, updated_daily_at.
- `OptionContract`: ticker, expiración, strike, right (P/C), multiplicador, conId, DTE.
- `ContractSnapshot`: contrato + bid, ask, last, delta, iv, oi, spread_pct, yield, yield_annualized, iv_rank, iv_percentile, initial_margin, `updated_at`.
- `Position`: instrumento, cantidad, valor de mercado, sector, expiración (si opción).
- `RiskStatus`: cushion, look_ahead, overnight, post_expiration, nivel semáforo por cada uno.
- `ScanCriteria`, `ScanResult`, `SectorExposure`, `SimulationResult`.

## 10. Comportamiento y manejo de errores

1. Un ticker que falla **no** detiene el lote; se registra el error y se muestra en la UI con motivo.
2. Datos nulos (bid/ask ≤ 0, NaN): el contrato queda **excluido del scanner** y se cuenta en un aviso.
3. Pérdida de conexión: reintento con backoff; la UI muestra estado "Desconectado" y datos con su timestamp.
4. Al cambiar real ↔ simulada: cerrar conexión, limpiar estado de cuenta en memoria y **no mezclar** datos de portfolio (separar en BD por `account_id`).
5. Fichero de watchlist inválido: error legible con la línea/celda problemática.
6. El refresco periódico no puede solaparse con el anterior; si tarda más de X min se omite el siguiente ciclo y se avisa.
7. Semáforo: si falta el dato de cushion, estado "Desconocido" (gris), nunca verde.

## 11. Tests automatizados

**Obligatorios (unitarios, sin TWS):**
- `metrics/*`: spread, yield, anualizado, IV Rank/Percentile (casos límite: max=min, historial corto, IV nula).
- `scanner/filters`: cada predicado por separado, combinaciones, valores en el límite (20% exacto, yield 1% exacto).
- `watchlist/parser`: separadores mezclados, duplicados, minúsculas, vacíos, Excel con cabecera / sin cabecera.
- `portfolio/cushion`: umbrales y fronteras (incluye huecos de Q-05).
- `portfolio/diversification` y `simulator`: pesos suman 100%, asignación de varios contratos, semanas sin contratos.
- `config/settings`: validación, campos faltantes, valores fuera de rango.
- `broker/rate_limiter`.

**Integración (con `FakeGateway` + SQLite en memoria):** actualización diaria incremental de IV (solo pide días nuevos), añadir ticker tras la ejecución diaria, ciclo de refresco completo, error en un ticker.

**Manual (requiere TWS paper):** smoke test de conexión, cadena de opciones, what-if, historial IV, VIX. Excluido de CI.

## 12. Notas de implementación IBKR [PROPUESTA — verificar contra la documentación de la API]

- Sector/categoría: `reqContractDetails` → `industry`, `category`, `subcategory`.
- Historial IV: `reqHistoricalData` con `whatToShow="OPTION_IMPLIED_VOLATILITY"`, barras diarias, 1 año.
- OI y griegas: ticks genéricos (p. ej. 101 para OI de opciones) y `modelGreeks`.
- Ex-dividendo: tick genérico 456 (IB Dividends) o datos fundamentales (Q-12).
- Margen inicial: orden *what-if* (`whatIfOrder`, campos `initMarginChange`). **No es aditivo** entre contratos; se aproxima sumando (Q-13 resuelta).
- Cushion y liquidez: `accountSummary` / `accountValues` (`Cushion`, `LookAheadExcessLiquidity`, `LookAheadInitMarginReq`, `LookAheadMaintMarginReq`, `PostExpirationExcessLiquidity`, `PostExpirationMargin`). El valor "Overnight" **sí está expuesto** en la API según el usuario (Q-05b): localizar el tag exacto en la documentación; no inventarlo.
- VIX: índice `VIX` (CBOE) + futuros `VX` (CFE); requieren suscripciones (Q-11).

---

## 13. PENDIENTES — información faltante, ambigüedades y contradicciones

| ID | Punto | Texto original / problema | Pregunta concreta | Defecto provisional |
|----|-------|---------------------------|-------------------|---------------------|
| Q-01 | Tipo de operación | **RESUELTA**: solo puts vendidas; la app nunca envía órdenes (solo what-if). | — | — |
| Q-02 | Plataforma/UI | No se indica tipo de aplicación. | ¿Web local, escritorio o Excel/terminal? ¿Preferencia de lenguaje? | Python + FastAPI/HTMX local — **ACEPTADO por el usuario** |
| Q-03 | DTE | **RESUELTA**: Regular 25-35 (configurable). Táctica: máximo 15 días, configurable. Se interpreta como DTE ≤ 15 (inclusive) y sin mínimo salvo DTE ≥ 1, salvo que se indique lo contrario. | — | — |
| Q-04 | % strike | **RESUELTA (2026-09-29)**: el descuento es un valor **mínimo** (strike al menos X % por debajo): Regular 20 %, Táctica 10 %, ambos editables. El máximo es el límite de lo guardado (45 %). Se guardan strikes de −10 % a −45 % (cambiado por el usuario desde −15 %, para que la Táctica al 10 % tenga datos) y DTE hasta 60. | — | — |
| Q-05 | Semáforo Cushion | **RESUELTA**: >40% Normal (verde); 30%-40% Preocupación (ámbar); <30% Riesgo alto (rojo). Fronteras confirmadas: 40% exacto → ámbar, 30% exacto → rojo. | — | — |
| Q-05b | Look Ahead / Overnight / Post-Expiration | **RESUELTA (2026-09-29)**: Overnight no existe en la API, se sustituye por `HighestSeverity` (0 Normal verde, 1 Advertencia/margen bajo ámbar, 2 Riesgo elevado/cerca del límite naranja, 3 Liquidación/margen crítico rojo; el usuario escribió "0 Normal rojo", se interpreta como verde, y no indicó color para 2 y 3). Cushion actual = tag `Cushion` de IBKR. Look Ahead y Post-Expiration = `Excess / NetLiquidation`. **Regla**: `PostExpirationExcess` = 0 se trata como "Sin datos" (gris). **Observado**: en la cuenta simulada `HighestSeverity` no llega (se muestra "Sin datos") y `PostExpirationExcess` vale 0. | — | — |
| Q-06 | Definición de yield | **Parcialmente resuelta**: prima = mid (bid+ask)/2. Se mantienen provisionales: anualizado lineal (×365/DTE); spread % sobre mid; "Max Spread" en %. | ¿Confirmas los tres provisionales? | lineal; mid; % — **ACEPTADO por el usuario** |
| Q-07 | IV Rank / Percentile | ¿Ventana 252 días de trading o 365 naturales? ¿IV del subyacente (30d constante) de IBKR? Se usa el mismo valor para todos los contratos del ticker. | Confirmar | 1 año, IV del subyacente — **ACEPTADO por el usuario** |
| Q-08 | "Cada X minutos" | **RESUELTA**: X = 5 min por defecto, configurable (`refresh.interval_minutes`). Se incluye botón de refresco manual [PROPUESTA]. | — | — |
| Q-09 | Ejecución diaria | **RESUELTA**: sin hora determinada. La actualización diaria se ejecuta al arrancar la app si no se ha hecho ya hoy, y bajo demanda (botón manual y al añadir tickers nuevos, RF-05). El planificador no dispara la actualización diaria a una hora fija. | — | — |
| Q-10 | Base de pesos | **RESUELTA**: base = Exposición Potencial Total (EPT) = valor de acciones + exposición nominal de puts vendidas (§8). Se ignora cash. | — | — |
| Q-10b | Peso respecto a la misma semana | **RESUELTA**: nominal del candidato / nominal total de puts vendidas de esa semana (incluyendo el candidato). | — | — |
| Q-11 | Suscripciones y datos | **RESUELTA**: opciones en tiempo real; VIX y futuros VIX pueden ser diferidos. Si faltan permisos, error explícito por ticker. | — | — |
| Q-12 | Ex-dividendo, sector | "Sector" y "Categoría": ¿los de IBKR (industry/category)? ¿Fuente para ex-dividendo? | Confirmar uso exclusivo de IBKR | Solo IBKR — **ACEPTADO por el usuario** |
| Q-13 | Simulación de margen | **RESUELTA**: se acepta una **aproximación** del margen total (suma de what-if individuales por contrato, marcada como "aproximado" en la UI). | — | — |
| Q-14 | Simulación de asignación | "Si se ejercieran" esos contratos. | ¿Se asume asignación al 100% de todos, con qué cantidad de contratos (input del usuario)? ¿Se calcula también escenario de caída de mercado? | 100% asignación, cantidad = input — **ACEPTADO por el usuario** |
| Q-15 | Diversificación 5 semanas | "Próximas 5 semanas en las que se tengan contratos abiertos": ¿5 semanas naturales desde hoy, o las 5 próximas que tengan contratos? | Aclarar. Y qué métrica se muestra por semana (nocional de asignación por sector). | 5 semanas naturales; nocional — **ACEPTADO por el usuario** |
| Q-16 | VIX futuros | "Valor previsto de futuros 2-3 semanas": los futuros VX son mensuales/semanales. | ¿Cuántos vencimientos y cuáles (próximos 2-3 disponibles)? | Próximos 3 vencimientos — **ACEPTADO por el usuario** |
| Q-17 | Volumen de datos | Tamaño de la watchlist y nº de contratos candidatos por ticker no indicados; el límite de líneas de mercado (por defecto 100) puede impedir refrescar todo en streaming. | ¿Cuántos tickers (~)? ¿Límite de contratos candidatos por ticker? | Snapshots por lotes; límite configurable — **ACEPTADO por el usuario** |
| Q-18 | Moneda | Cuenta irlandesa: moneda base posible EUR con subyacentes USD. | ¿Moneda base de la cuenta y conversión a mostrar? | Usar moneda base de la cuenta y convertir con FX de IBKR — **ACEPTADO por el usuario** |
| Q-19 | Idioma | Interfaz. | ¿Español? | Español — **ACEPTADO por el usuario** |
| Q-20 | Alertas | No mencionadas. | ¿Se desean notificaciones (p. ej. cushion <25%)? | No (fuera de alcance) — **ACEPTADO por el usuario** |
| Q-21 | Exportación | No mencionada. | ¿Exportar resultados a Excel/CSV? | Fuera de alcance (ampliación futura) — **ACEPTADO por el usuario** |

**Contradicciones/erratas detectadas:** DTE "25 a 25"; DTE táctica sin número; hueco 30-40% en semáforo; "Riesgo algo"; "Overnigh"/"Post-Expirity" (se interpretan como *Overnight* y *Post-Expiration*); "exdividendo" (ex-dividend); "L20: guaradado".

## 14. Ampliaciones futuras previstas (no implementar ahora)

Nuevas estrategias en el scanner (calls cubiertas, spreads) añadiendo `ScanCriteria`/filtros sin tocar el motor; otros brokers vía nueva implementación de `BrokerGateway`; alertas; exportación; más escenarios de simulación (estrés de mercado).

## 15. Orden de implementación sugerido

1. `config`, `domain`, `metrics` + tests.
2. `watchlist` + tests.
3. `storage`.
4. `broker` (interfaz + `FakeGateway`), luego `IBKRGateway`.
5. `jobs/daily_update`, `jobs/refresh`.
6. `scanner`.
7. `portfolio` (riesgo, diversificación, simulador).
8. `market/vix`.
9. UI.
10. Pruebas manuales con TWS simulada.

**Criterio de "hecho" por módulo:** tests unitarios en verde, sin acceso a red en tests unitarios, errores devueltos con mensajes claros, cero valores de negocio hardcodeados.
