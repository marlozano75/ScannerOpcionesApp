<#
  Rutina diaria de las fechas de apertura: pide al conector de Tortuga (por la CLI de Claude, sin interfaz) tus ejecuciones de apertura
  de puts vendidas y las deja en un JSON. La aplicación lo lee para saber con qué DTE abriste cada put y asignarla a su perfil en el
  Panel (IBKR no da la fecha de apertura). Solo sustituye el JSON si el nuevo es válido; el log queda en logs\aperturas.log.

  Uso:   powershell -ExecutionPolicy Bypass -File scripts\actualizar_aperturas.ps1 [-Salida data\community\aperturas.json] [-Modelo sonnet]
  Hace falta: la CLI `claude` con la sesión iniciada y el conector de Tortuga Trades conectado (solo lectura: get_context y list_trades).
  La rutina de la comunidad (scripts\actualizar_comunidad.ps1) la lanza cada día, así que no necesita tarea programada propia.
#>
param(
    [string]$Salida = "data\community\aperturas.json",
    [string]$Modelo = "sonnet"
)
$ErrorActionPreference = "Stop"
$raiz = Split-Path -Parent $PSScriptRoot
Set-Location $raiz
$utf8 = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = Join-Path $raiz "logs\aperturas.log"

function Anotar([string]$mensaje) {
    $linea = "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $mensaje
    Add-Content -Path $log -Value $linea -Encoding UTF8
    Write-Host $linea
}

$python = Join-Path $raiz ".venv\Scripts\python.exe"
$prompt = [IO.File]::ReadAllText((Join-Path $PSScriptRoot "aperturas_prompt.md"), $utf8)
$herramientas = "mcp__claude_ai_Tortuga_Trades__get_context,mcp__claude_ai_Tortuga_Trades__list_trades"
$tmp = Join-Path $env:TEMP ("aperturas_" + [guid]::NewGuid().ToString("N") + ".json")

try {
    Anotar "Pidiendo tus aperturas a Tortuga (modelo $Modelo)"
    $texto = ($prompt | & claude -p --model $Modelo --allowedTools $herramientas --max-turns 25 --output-format text) -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "claude terminó con el código $LASTEXITCODE" }
    $ini = $texto.IndexOf("{"); $fin = $texto.LastIndexOf("}")
    if ($ini -lt 0 -or $fin -le $ini) { throw "la respuesta no contiene un JSON: " + $texto.Substring(0, [Math]::Min(200, $texto.Length)) }
    [IO.File]::WriteAllText($tmp, $texto.Substring($ini, $fin - $ini + 1), $utf8)    # sin BOM
    $resultado = & $python -m scanner_opciones.portfolio.openings $tmp 2>&1
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
