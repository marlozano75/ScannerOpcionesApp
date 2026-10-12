<#
  Programa en el Programador de tareas de Windows la rutina diaria de la comunidad (scripts\actualizar_comunidad.ps1).
  Uso:   powershell -ExecutionPolicy Bypass -File scripts\programar_comunidad.ps1 [-Hora 11:15]
  La hora es la local del equipo. Si el equipo estaba apagado a esa hora, la tarea se ejecuta en cuanto se enciende
  (StartWhenAvailable). Para quitarla:  Unregister-ScheduledTask -TaskName "ScannerOpciones-Comunidad" -Confirm:$false
#>
param([string]$Hora = "11:15")
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "actualizar_comunidad.ps1"
$accion = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$disparador = New-ScheduledTaskTrigger -Daily -At $Hora
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20)
Register-ScheduledTask -TaskName "ScannerOpciones-Comunidad" -Action $accion -Trigger $disparador -Settings $ajustes `
    -Description "Actualiza el JSON de la comunidad de Tortuga Trades para el Scanner de opciones" -Force | Out-Null
Write-Host "Programada cada día a las $Hora. Prueba ahora:  Start-ScheduledTask -TaskName ScannerOpciones-Comunidad"
