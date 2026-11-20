<#
.SYNOPSIS
    Inicia AutoClip Publisher — Personal Edition en Windows (FastAPI + Worker supervisados).
.DESCRIPTION
    Arranca simultáneamente el servidor web FastAPI en el puerto 8000 y el worker
    persistente de publicación como procesos independientes supervisados con apagado limpio.
#>

[CmdletBinding()]
param(
    [int]$Port = 8000,
    [string]$HostAddress = "127.0.0.1",
    [double]$WorkerInterval = 5.0,
    [switch]$WorkerOnly,
    [switch]$WebOnly
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  AUTOCLIP PUBLISHER — PERSONAL EDITION (SUPERVISADO)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    Write-Host "[!] Entorno virtual .venv no encontrado. Creando con uv..." -ForegroundColor Yellow
    uv venv --python 3.11 .venv
    uv pip install -e .
}

# Verificar token de propietario (nunca usar valores fijos predeterminados)
$TokenCheck = & $PythonExe -c "from clipflow.security import get_owner_token; print('OK' if get_owner_token() else 'MISSING')"
if ($TokenCheck.Trim() -ne "OK") {
    Write-Host "[!] OWNER_TOKEN no encontrado en .env ni en data/.owner.token." -ForegroundColor Yellow
    Write-Host "[*] Generando token aleatorio criptografico en data/.owner.token..." -ForegroundColor Cyan
    & $PythonExe -c "from clipflow.security import ensure_owner_token_file; ensure_owner_token_file(); print('[+] Token seguro guardado en data/.owner.token (consulta ese archivo local para iniciar sesion).')"
}

Write-Host "[*] Inicializando base de datos SQLite y verificando esquema..." -ForegroundColor Gray
& $PythonExe -c "from clipflow.db import init_db; init_db(); print('[+] Base de datos lista.')"

$SupArgs = @(
    "-m", "clipflow.supervisor",
    "--host", $HostAddress,
    "--port", "$Port",
    "--interval", "$WorkerInterval"
)
if ($WorkerOnly) { $SupArgs += "--worker-only" }
if ($WebOnly)    { $SupArgs += "--web-only" }

if ($WorkerOnly) {
    Write-Host "[+] Iniciando solo Worker persistente supervisado..." -ForegroundColor Green
} elseif ($WebOnly) {
    Write-Host "[+] Iniciando solo Panel Web en http://${HostAddress}:${Port}" -ForegroundColor Green
} else {
    Write-Host "[+] Iniciando Panel Web (http://${HostAddress}:${Port}) + Worker simultaneamente..." -ForegroundColor Green
}
Write-Host "[*] Presiona Ctrl+C para un apagado limpio de ambos procesos." -ForegroundColor Gray

& $PythonExe @SupArgs
