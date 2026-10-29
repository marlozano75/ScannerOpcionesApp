# Escaneo automático de perfiles y registro (2026-10-29)

Cada `scan_log.interval_minutes` (15) `AppService.scan_log_cycle` → `jobs/scan_log.py::ScanLogJob` escanea los perfiles elegidos con los filtros
GRABADOS en cada uno (`profile_specs`, los mismos de `/scanner/todos`; put y spread) y registra en SQLite (`storage/scan_log.py`, migración v30):

- `scan_log`: un **episodio** por perfil y contrato (o spread: con `long_strike`): `first_seen`, `last_seen`, `ended_at`, `end_reason`, `active`, y precio/yield/DTE/descuento
  al aparecer y la última vez que se vio. Si vuelve a cumplir tras desaparecer, se abre un episodio nuevo. Un contrato que cumple dos perfiles se anota una vez por cada uno.
- `scan_events`: `aparece` / `desaparece` con fecha y perfil. El **motivo** de la desaparición es el filtro que lo descarta ahora (`ScanOutput.rejections`); si ningún filtro
  lo evalúa: «vencido», «el spread ya no se forma…» o «sin cotización o fuera de los contratos guardados».
- `scan_runs`: cada pasada por perfil (cumplen, nuevos, desaparecen, tickers con el mercado cerrado).

**Mercado abierto por ticker** (`AppService.ticker_market_open`): el de EE. UU. o, para ESTX50 y los valores europeos, su horario propio (`settings.instruments`). Los tickers con el
mercado cerrado se dejan como estaban (ni aparecen ni desaparecen): sus cotizaciones no cambian y darían falsas salidas.

**Google Sheets** (`scan_log.sheets`, `jobs/scan_log_sheets.py`; opcional): pestañas `Eventos` y `Escaneos` (se añaden filas nuevas) y `Activos` (se reescribe). Necesita
`gspread` y una cuenta de servicio de Google con la hoja compartida como editor (clave JSON en `config/google-credentials.json`, ignorada por git). Si Google falla, lo pendiente
(`synced = 0`) se reintenta en la pasada siguiente y el registro en SQLite no se pierde. No verificado contra un Google Sheet real.
