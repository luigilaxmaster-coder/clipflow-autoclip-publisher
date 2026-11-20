<#
.SYNOPSIS
    Inicia AutoClip Publisher — Personal Edition en Windows.
.DESCRIPTION
    Arranca el servidor web FastAPI del panel de administración en el puerto 8000
    y lanza el worker persistente de la cola de publicación.
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [switch]$WorkerOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  AUTOCLIP PUBLISHER — PERSONAL EDITION (2026)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "[!] Entorno virtual .venv no encontrado. Creando con uv..." -ForegroundColor Yellow
    uv venv --python 3.11 .venv
    uv pip install -e .
}

# Inicializar Base de Datos
Write-Host "[*] Inicializando base de datos SQLite y claves seguras..." -ForegroundColor Gray
& $PythonExe -c "from clipflow.db import init_db; init_db(); print('DB lista.')"

if ($WorkerOnly) {
    Write-Host "[+] Iniciando worker persistente de publicación..." -ForegroundColor Green
    & $PythonExe -m clipflow.cli worker --interval 5
} else {
    Write-Host "[+] Abriendo panel web en http://${HostAddress}:${Port}" -ForegroundColor Green
    Write-Host "[*] Presiona Ctrl+C para detener." -ForegroundColor Gray
    & $PythonExe -m uvicorn clipflow.web.app:app --host $HostAddress --port $Port --reload
}
