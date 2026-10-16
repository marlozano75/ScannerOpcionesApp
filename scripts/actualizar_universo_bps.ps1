<#
  Rutina diaria del universo para bull put spreads: pide al Market Radar de Tortuga (por la CLI de Claude, sin interfaz) los valores con
  buena liquidez de opciones y deja data\universe\universo_bps.json. La composición (lista inicial universo\seed_bps.json, histéresis de
  1 día para entrar y 5 días hábiles para salir, exclusiones) la decide Python, no el modelo. La aplicación lo relee solo si cambia el fichero y lo
  vuelca en la fuente «Universo_BPS» del Universo (apta para spreads). Solo sustituye el JSON si el nuevo es válido; log en logs\universo_bps.log.

  Uso:   powershell -ExecutionPolicy Bypass -File scripts\actualizar_universo_bps.ps1 [-Salida data\universe\universo_bps.json] [-Modelo sonnet]
  Hace falta: la CLI `claude` con la sesión iniciada y el conector de Tortuga Trades conectado (solo lectura).
  Programarla cada día: scripts\programar_universo_bps.ps1
#>
param(
    [string]$Salida = "data\universe\universo_bps.json",
    [string]$Modelo = "sonnet"
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $raiz "logs\universo_bps.log"

function Anotar([string]$mensaje) {
    $linea = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $mensaje
    Add-Content -Path $log -Value $linea -Encoding UTF8
    Write-Host $linea
}

$python = Join-Path $raiz ".venv\Scripts\python.exe"
$prompt = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "universo_bps_prompt.md"), $utf8)
$herramientas = "mcp__claude_ai_Tortuga_Trades__get_context,mcp__claude_ai_Tortuga_Trades__get_market_radar"
$tmp = Join-Path $env:TEMP ("radar_bps_" + [guid]::NewGuid().ToString("N") + ".json")

try {
    Anotar "Pidiendo el Market Radar a Tortuga (modelo $Modelo)"
    $texto = ($prompt | & claude -p --model $Modelo --allowedTools $herramientas --max-turns 15 --output-format text) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "claude terminó con el código $LASTEXITCODE" }
    $ini = $texto.IndexOf("{"); $fin = $texto.LastIndexOf("}")
    if ($ini -lt 0 -or $fin -le $ini) { throw "la respuesta no contiene un JSON: " + $texto.Substring(0, [Math]::Min(200, $texto.Length)) }
    [IO.File]::WriteAllText($tmp, $texto.Substring($ini, $fin - $ini + 1), $utf8)    # sin BOM
    $destino = if ([IO.Path]::IsPathRooted($Salida)) { $Salida } else { Join-Path $raiz $Salida }
    # construir valida el radar, aplica la lista inicial y la histéresis, y solo escribe el universo (y el historial) si todo es válido
    $resultado = & $python -m scanner_opciones.universe_bps construir $tmp $destino "config\config.yaml" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "no se actualiza, se conserva el anterior: $resultado" }
    Anotar "Actualizado $destino ($($resultado -join ' '))"
}
catch {
    Anotar "ERROR: $($_.Exception.Message)"
    exit 1
}
finally {
    if (Test-Path $tmp) { Remove-Item -Force $tmp }
}
