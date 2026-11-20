<#
.SYNOPSIS
    Actualiza el sistema AutoClip Publisher y verifica dependencias y base de datos.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "[*] Comprobando actualizaciones de git..." -ForegroundColor Cyan
git status

Write-Host "[*] Verificando dependencias con uv..." -ForegroundColor Cyan
uv pip install -e .

Write-Host "[*] Comprobando integridad de base de datos..." -ForegroundColor Cyan
$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
& $PythonExe -c "from clipflow.db import init_db; init_db(); print('[+] Base de datos y tablas al dia.')"

Write-Host "[*] Ejecutando suite de validacion pytest..." -ForegroundColor Cyan
& $PythonExe -m pytest -v --tb=short
