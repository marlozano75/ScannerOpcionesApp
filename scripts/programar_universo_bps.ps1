<#
  Programa en el Programador de tareas de Windows la rutina diaria del universo para bull put spreads (scripts\actualizar_universo_bps.ps1).
  Uso:   powershell -ExecutionPolicy Bypass -File scripts\programar_universo_bps.ps1 [-Hora 11:30]
  La hora es la local del equipo. Si el equipo estaba apagado a esa hora, la tarea se ejecuta en cuanto se enciende (StartWhenAvailable).
  Para quitarla:  Unregister-ScheduledTask -TaskName "ScannerOpciones-UniversoBPS" -Confirm:$false
#>
param([string]$Hora = "11:30")
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "actualizar_universo_bps.ps1"
$accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName "ScannerOpciones-UniversoBPS" -Action $accion -Trigger $disparador -Settings $ajustes `
    -Description "Actualiza el universo para bull put spreads (Market Radar de Tortuga) del Scanner de opciones" -Force | Out-Null
Write-Host "Programada cada día a las $Hora. Prueba ahora:  Start-ScheduledTask -TaskName ScannerOpciones-UniversoBPS"
