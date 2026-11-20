<#
.SYNOPSIS
    Ejecuta el respaldo atómico de la base de datos y credenciales cifradas.
#>

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
& $PythonExe scripts/backup.py
