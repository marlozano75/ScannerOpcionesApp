<#
  Rutina diaria de la comunidad de Tortuga Trades: pide al conector de Tortuga (por la CLI de Claude, sin interfaz) los tickers
  más vendidos y las aperturas de los últimos días, los deja en un JSON y la aplicación lo lee (fuente «Tortuga» del Universo,
  columna «Comunidad» del Scanner). Solo sustituye el JSON si el nuevo es válido; el log queda en logs\comunidad.log.

  Uso:   powershell -ExecutionPolicy Bypass -File scripts\actualizar_comunidad.ps1 [-Salida data\community\tortuga.json] [-Modelo sonnet]
  Hace falta: la CLI `claude` con la sesión iniciada y el conector de Tortuga Trades conectado (solo lectura).
  Programarla cada día: scripts\programar_comunidad.ps1
#>
param(
    [string]$Salida = "data\community\tortuga.json",
    [string]$Modelo = "sonnet"
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $raiz "logs\comunidad.log"

function Anotar([string]$mensaje) {
    $linea = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $mensaje
    Add-Content -Path $log -Value $linea -Encoding UTF8
    Write-Host $linea
}

# Fechas de apertura de tus puts vendidas (aperturas.json): si falla no afecta a la comunidad
try { & (Join-Path $PSScriptRoot "actualizar_aperturas.ps1") -Modelo $Modelo } catch { Anotar "Aperturas: $($_.Exception.Message)" }

$python = Join-Path $raiz ".venv\Scripts\python.exe"
$prompt = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "comunidad_prompt.md"), $utf8)
$herramientas = "mcp__claude_ai_Tortuga_Trades__get_context,mcp__claude_ai_Tortuga_Trades__get_community_screener,mcp__claude_ai_Tortuga_Trades__list_public_operations"
$tmp = Join-Path $env:TEMP ("tortuga_" + [guid]::NewGuid().ToString("N") + ".json")

try {
    Anotar "Pidiendo los datos a Tortuga (modelo $Modelo)"
    $texto = ($prompt | & claude -p --model $Modelo --allowedTools $herramientas --max-turns 25 --output-format text) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "claude terminó con el código $LASTEXITCODE" }
    $ini = $texto.IndexOf("{"); $fin = $texto.LastIndexOf("}")
    if ($ini -lt 0 -or $fin -le $ini) { throw "la respuesta no contiene un JSON: " + $texto.Substring(0, [Math]::Min(200, $texto.Length)) }
    [IO.File]::WriteAllText($tmp, $texto.Substring($ini, $fin - $ini + 1), $utf8)    # sin BOM
    $resultado = & $python -m scanner_opciones.community $tmp 2>&1
    if ($LASTEXITCODE -ne 0) { throw "JSON no válido, se conserva el anterior: $resultado" }
    $destino = if ([IO.Path]::IsPathRooted($Salida)) { $Salida } else { Join-Path $raiz $Salida }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destino) | Out-Null
    Move-Item -Force -Path $tmp -Destination $destino
    Anotar "Actualizado $destino ($($resultado -join ' '))"
}
catch {
    Anotar "ERROR: $($_.Exception.Message)"
    exit 1
}
finally {
    if (Test-Path $tmp) { Remove-Item -Force $tmp }
}
