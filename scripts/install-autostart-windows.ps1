<#
.SYNOPSIS
    Registra AutoClip Publisher (Web + Worker) para arranque automático 24/7 en Windows (PC dedicado).
.DESCRIPTION
    Crea una tarea programada en el Programador de Tareas de Windows (Task Scheduler)
    para iniciar automáticamente scripts/run-publisher.ps1 al iniciar sesión, sin límite
    de duración y con reinicio automático si ocurre algún fallo inesperado.
#>

[CmdletBinding()]
param(
    [string]$TaskName = "AutoClipPublisher24x7",
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$RunnerScript = Join-Path $ScriptDir "run-publisher.ps1"

if ($Uninstall) {
    Write-Host "[*] Eliminando tarea programada '$TaskName'..." -ForegroundColor Yellow
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "[+] Tarea eliminada." -ForegroundColor Green
    exit 0
}

if (-not (Test-Path $RunnerScript)) {
    throw "No se encontró el lanzador esperado: $RunnerScript"
}

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    throw "No se encontró el entorno virtual .venv. Ejecuta primero .\scripts\setup-windows.ps1"
}

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$RunnerScript`"" `
    -WorkingDirectory $ProjectDir

$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -RestartCount 5 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Description "AutoClip Publisher Personal Edition — Servidor Web + Worker 24/7" `
    -Force | Out-Null

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  AUTO-ARRANQUE 24/7 CONFIGURADO: $TaskName" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "[+] Usuario      : $env:USERNAME"
Write-Host "[+] Directorio   : $ProjectDir"
Write-Host "[+] Lanzador     : $RunnerScript"
Write-Host "[*] Para iniciarla ahora manualmente: Start-ScheduledTask -TaskName '$TaskName'" -ForegroundColor Gray
Write-Host "[*] Para desinstalarla             : .\scripts\install-autostart-windows.ps1 -Uninstall" -ForegroundColor Gray
