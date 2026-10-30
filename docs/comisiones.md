# Comisiones de IBKR en la rentabilidad (2026-10-30)

`metrics/commissions.py::CommissionModel`, `commissions:` en la configuración (`CommissionSettings`), `ScanCriteria.commission` / `exit_commission` / `cost_ps`.

**Qué se medió (cuenta INDIVIDUAL, IB Gateway).** El what-if de IBKR NO sirve: con tarifa escalonada devuelve `commission` vacío (1,8e308) y un rango
`minCommission`–`maxCommission` muy ancho (0,56–1,55 $ para 1 contrato, 4,1–24 $ para 20). Las ejecuciones reales (60 últimas de opciones, Tortuga `list_trades`)
cuadran con la tarifa fija de IBKR Pro: **0,65 $/contrato, mínimo 1,00 $ por orden, más ≈ 0,05 $ de tasas** por contrato y pata. 1 contrato suelto ≈ 1,04–1,07 $; 2 contratos
≈ 1,36 $; un spread enviado como UNA orden combo ≈ 0,55 $ por pata (≈ 1,10 $: un solo mínimo repartido). La venta suma ~0,01–0,03 $ más por las tasas que dependen de
la prima. Algunas ejecuciones salen con importes raros (abonos o fills parciales): se tratan como ruido. Los parámetros se pueden recalibrar en `commissions:`.

**Dónde se resta (prima neta = prima de referencia − comisiones por acción).**
- Scanner (put): yield del periodo y anual (también «Yield bid»), filtros de yield mínimo, crédito mínimo y capital en riesgo, columnas Crédito, Riesgo %NLV, Precio neto y Dist. neto.
- Scanner (spread): crédito = vendida − ask de la comprada − comisión de UNA orden combo; el crédito/ancho, el retorno/riesgo, la pérdida máxima y el breakeven salen de ese crédito.
- **Descuento (opción B):** con comisiones es la distancia del **punto de equilibrio** (strike − prima neta) por debajo del precio, no la del strike. El mínimo editable se compara con
  ese descuento; el máximo (no editable) sigue siendo la distancia del strike, porque es el límite de lo que se guarda. En un spread, el breakeven es strike vendido − crédito neto.
  Con `commissions.enabled: false` todo vuelve a la prima bruta y al descuento del strike.
- **Comisión de cierre:** los perfiles cuya estrategia de salida no mantiene hasta el vencimiento (`ExitRules.holds_to_expiry` falso: Tastytrade recoge al 50 %, revisa a 21 DTE) descuentan
  también la de recomprar (`ScanCriteria.exit_commission`, la pone `parse_scan` según el perfil). TPC y los filtros sin perfil solo descuentan la de apertura.
- Panel: «Capturado» y «Prima capt.» de las puts cuyo perfil sale antes del vencimiento descuentan la comisión de recomprar (con sus contratos reales; un spread, una orden combo): el aviso
  «Recoger beneficios» salta cuando lo que te quedaría NETO alcanza el objetivo. La de apertura ya está pagada y no se resta.
- Comunidad (Ideas): prima, yield y descuento de hoy netos de la apertura; la comparación con lo que cobró el trader (estado «Prima menor») se hace en bruto.
- Radar de Tortuga: `put_mid`, `spread_premium`, el rendimiento anualizado del spread y las distancias son netos (`RadarRow.net_of_commissions`; los resta `AppService.radar_spreads`).

**Límites.** Se supone 1 contrato por orden (`commissions.contracts`; con más, el mínimo se reparte y la comisión por contrato baja). Solo opciones de EE. UU. (multiplicador 100): ESTX50 y los
valores europeos tienen otras tarifas y no se tocan. Ideas y el radar usan solo la comisión de apertura (no hay perfil por fila). El escaneo de todos los perfiles y el registro del
Histórico usan el `exit_commission` de cada perfil. Los rolls (`_roll.html`) siguen sin comisiones.
