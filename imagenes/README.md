# Imágenes y material de referencia

Capturas y notas que sirven de referencia de diseño o de criterios. No las usa la app. Al añadir un archivo nuevo, apúntalo aquí.

## Referencias de SpreadVector (spreadvector.com, capturas de un vídeo)

Inspiran los filtros técnicos y los gráficos del Scanner (RF-44 en `ESPECIFICACION_TECNICA.md`).

| Archivo | Qué muestra |
| :--- | :--- |
| `Trendandlevels.png` | Bloque «Trend and levels»: filtro de tendencia y zona de soporte/resistencia (primera referencia del rediseño) |
| `Min_Days_Since_Last_Touch.png` | Tarjeta «Min days since strike last visited» |
| `TrendFilter_Support_SpreadVector.png` | Filtro de tendencia (Off + ventana de 4 meses) y «Require a tested price zone» (3+ toques, banda 1,5 %, 2+ grupos, último año) |
| `Captura_SpreadVector_RangosUptrend.png` | Desplegable de la ventana de tendencia: 1 semana a 1 año |
| `Min_Days_Since_Strike_last_Visited_SpreadVector.png` | «Min days since strike last visited» en «Any»: excluye strikes tocados por un cierre dentro del plazo mínimo |
| `Min_Days_Since_Strike_last_Visited_SpreadVector_Valores.PNG` | Opciones del plazo mínimo: 10, 20, 30, 45, 60, 90, 120, 180, 252 y 365+ días |
| `Strikes_SpreadVector.PNG` | Selector de delta del strike corto (10–30 o cualquiera) y bloques de vencimiento y rango de crédito |
| `Strike History_SpreadVector.png` | «Strike history»: cierres mensuales de 24 meses frente al strike, con el aviso «24 of 24 months above» |
| `GraficoCierres y UltimoDíaDeVisita_SpreadVector.png` | Gráfico de cierres con el strike y el último día en que se tocó (círculo naranja, «10 days ago») |
| `Graficos_SpreadVector.PNG` | Gráfico de último toque junto a la cadena de opciones (strike, mid, delta, theta, IV) |
| `IgnoreEarnings&Ignore Expected Move Filter.png` | «Screener defaults»: ignorar el filtro de resultados y el de movimiento esperado (la exclusión por resultados está activa por defecto) |

## Capturas propias de la app

| Archivo | Qué muestra |
| :--- | :--- |
| `HighestSeverity.png` | Indicador de riesgo `HighestSeverity` de IBKR |
| `Errores_tickets desconocidos o sin cadena de opciones.png` | Aviso de la Watchlist con tickers inservibles («Ticker no reconocido por IBKR», «Sin cadena de opciones»). Motivó el aviso que se cierra con «×» (commit 35c1bbb) |

## Notas

| Archivo | Qué contiene |
| :--- | :--- |
| `Criterios de Calidad-Universo.txt` | Resumen de los criterios de deuda y flujo de caja (D/E, cobertura de intereses, efectivo/deuda corriente, flujo operativo/deuda, CapEx, recompra neta, FCF/activos) con sus umbrales y rendimientos históricos. Base de los filtros de solvencia del Universo (`scanner.quality.thresholds`) |
