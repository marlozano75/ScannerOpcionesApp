# Pestaña «Backtest» (2026-11-08)

Pestaña `/backtest` con **todo lo probado** para decidir cuándo entrar (detección de suelos, entrada en un CDS, venta de puts), con sus resultados, veredicto y enlaces a los scripts. Es un registro que seguirá creciendo: cada estrategia nueva que se pruebe se añade como una ronda.

- Solo lee y muestra: no depende de TWS, de tastytrade ni de la base de datos; no calcula nada. Los números salen de los scripts de `scripts/suelos/` (ejecutados a mano) y se transcriben al catálogo.
- Código: `backtest/catalog.py` (lectura y validación), `ui/routes_backtest.py` (ruta, con filtro `?objetivo=suelos|cds|puts|datos`) y `ui/templates/backtest.html`. Los datos están en `backtest/resultados.json`, incluido como `package-data`.
- El fichero se vuelve a leer en cada petición: añadir una ronda no requiere reiniciar la app. Si el JSON tiene un error (celdas de más, objetivo desconocido, id repetido…) la pestaña muestra el mensaje en lugar de romperse; `tests/unit/test_backtest_catalog.py` valida el fichero real.
- Las celdas numéricas se ordenan por su valor (`data-sort`, que lee el primer número con formato español: «12,5 %», «+0,8 %», «106.000»); las tablas con `barra` dibujan un fondo proporcional en esa columna.
- El detalle largo de cada ronda sigue en `docs/deteccion-suelos.md`; la pestaña resume tablas y conclusiones.

## Cómo añadir una ronda

1. Ejecuta el experimento (los scripts van a `scripts/suelos/` con su línea en `scripts/suelos/README.md`) y apunta la muestra, la métrica y la base con la que se compara.
2. Añade un objeto al final de `rondas` en `backtest/resultados.json`:

```json
{
  "id": "R9",
  "fecha": "2026-11-20",
  "titulo": "Nombre corto de la estrategia",
  "objetivo": "cds",
  "pregunta": "Qué se quería saber.",
  "veredicto": "sin_ventaja",
  "veredicto_texto": "Frase corta con el resultado",
  "tablas": [
    {
      "titulo": "Resultados",
      "columnas": ["Señal", "n", "Gana la carrera", "Ret. 10 d"],
      "filas": [["Señal A", "1.234", "48,1 %", "+0,9 %"], ["**Señal B**", "560", "**52,0 %**", "+1,4 %"]],
      "barra": {"col": 2, "max": 60},
      "nota": "Base: 47,2 %."
    }
  ],
  "puntos": ["Qué se vio, cautelas y límites."],
  "scripts": ["scripts/suelos/nuevo.py"],
  "doc": "docs/deteccion-suelos.md"
}
```

   - `objetivo`: `suelos`, `cds`, `puts` o `datos`. `veredicto`: `ventaja`, `parcial`, `sin_ventaja` o `info`.
   - Las celdas son texto ya formateado (números con coma decimal y punto de millares); `**negrita**` y `` `código` `` son lo único que se interpreta, el resto se escapa.
   - Cada fila debe tener tantas celdas como `columnas`; `barra.col` es el índice (desde 0) de la columna con el número que se dibuja y `max` el valor que llena la barra.
3. Actualiza, si procede, `conclusiones`, `bases` y `pendientes` (y `actualizado`), y la sección de la ronda en `docs/deteccion-suelos.md` o el documento que corresponda.
4. `pytest tests/unit/test_backtest_catalog.py` comprueba el formato.

## Rondas actuales (2026-11-06)

R1 señales de suelo clásicas · R2 caída en ATR, Mogalef, MACD y puntuación de suelo · R3 datos intradía · R4 señales de suelo para vender puts · R5 señales de entrada para CDS · R6 FVG de LuxAlgo y Volume Profile · R7 combinaciones con más del 65 % · R8 The Strat completo · D1 datos disponibles. Conclusión general: se localizan zonas de suelo, pero ninguna señal por ticker mejora la entrada en un CDS ni la venta de puts; lo único consistente es una condición de mercado (amplitud) o el estrés de mercado combinado con sobreventa, irregular por régimen.
