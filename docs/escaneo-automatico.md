# Escaneo automático de perfiles y registro (2026-10-29)

Cada `scan_log.interval_minutes` (15) `AppService.scan_log_cycle` → `jobs/scan_log.py::ScanLogJob` escanea los perfiles elegidos con los filtros
GRABADOS en cada uno (`profile_specs`, los mismos de `/scanner/todos`; put y spread) y registra en SQLite (`storage/scan_log.py`, migración v30; v31 añade las columnas de mercado):

- `scan_log`: un **episodio** por perfil y contrato (o spread: con `long_strike`): `first_seen`, `last_seen`, `ended_at`, `end_reason`, `active`, y precio/yield/DTE/descuento
  al aparecer y la última vez que se vio. Si vuelve a cumplir tras desaparecer, se abre un episodio nuevo. Un contrato que cumple dos perfiles se anota una vez por cada uno.
- Además de precio/yield/DTE/descuento, cada episodio y evento guarda (v31, 2026-10-30; `EXTRA_FIELDS`, también en el Sheet y en `/historico`): crédito y riesgo por contrato en $ (put: prima neta y (strike − prima) × multiplicador; spread: crédito y pérdida máxima), delta (y delta de la comprada en spreads), yield bid anual (solo puts), IV Rank, IV Percentile, spread bid/ask % y bid size de la pata vendida. El episodio guarda la última vez vista; los eventos los valores de ese momento. Los episodios anteriores a v31 quedan con esas columnas vacías. `SheetsWriter._append` reescribe la cabecera de cada pestaña para que gane las columnas nuevas.
- `scan_events`: `aparece` / `desaparece` con fecha y perfil. El **motivo** de la desaparición es el filtro que lo descarta ahora (`ScanOutput.rejections`); si ningún filtro
  lo evalúa: «vencido», «el spread ya no se forma…» o «sin cotización o fuera de los contratos guardados».
- `scan_runs`: cada pasada por perfil (cumplen, nuevos, desaparecen, tickers con el mercado cerrado).

**Mercado abierto por ticker** (`AppService.ticker_market_open`): el de EE. UU. o, para ESTX50 y los valores europeos, su horario propio (`settings.instruments`). Los tickers con el
mercado cerrado se dejan como estaban (ni aparecen ni desaparecen): sus cotizaciones no cambian y darían falsas salidas.

**Google Sheets** (`scan_log.sheets`, `jobs/scan_log_sheets.py`; opcional): pestañas `Eventos` y `Escaneos` (se añaden filas nuevas) y `Activos` (se reescribe). Necesita
`gspread` y una cuenta de servicio de Google con la hoja compartida como editor (clave JSON en `config/google-credentials.json`, ignorada por git). Si Google falla, lo pendiente
(`synced = 0`) se reintenta en la pasada siguiente y el registro en SQLite no se pierde. No verificado contra un Google Sheet real.

**OAuth en vez de cuenta de servicio** (`sheets.auth: oauth`, 2026-10-30): las organizaciones de Google Cloud recientes bloquean la creación de claves de cuentas de servicio
(`iam.disableServiceAccountKeyCreation`). Con `oauth` se usa un ID de cliente de tipo «Aplicación de escritorio» (`oauth_client_file`) y la propia cuenta de Google del usuario, sin
compartir la hoja con ningún email. Se autoriza UNA vez con `python -m scanner_opciones.jobs.scan_log_sheets` (abre el navegador y guarda `oauth_token_file`); el ciclo automático nunca
abre el navegador: sin token falla con ese aviso. Con la pantalla de consentimiento en «Prueba», Google caduca el token a los 7 días: publícala («En producción»). No verificado contra Google.

**Pestaña Histórico (2026-10-30; `ui/routes_historico.py`, `historico.html`, `AppService.set_scan_log_cfg`).** `/historico` configura sin reiniciar el escaneo automático (activado, intervalo, perfiles) y el volcado a Google Sheets (activado, ID de la hoja, tipo de acceso) y muestra las mismas pestañas y columnas que el Sheet (Eventos y Escaneos: las 500 últimas filas; Activos), más «Escanear ahora». Lo guardado va a `meta` (`scan_log_cfg`) y manda sobre `config.yaml`; las credenciales y rutas de ficheros siguen solo en `config.yaml`. El bucle de `main.py` late cada minuto y `scan_log_cycle` respeta lo activado y el intervalo.
