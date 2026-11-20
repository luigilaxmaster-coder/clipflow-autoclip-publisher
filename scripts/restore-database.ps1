<#
.SYNOPSIS
    Restaura la base de datos de AutoClip Publisher desde un archivo ZIP de respaldo verificado.
#>

[CmdletBinding()]
param(
    [string]$BackupZip
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if ($BackupZip) {
    & $PythonExe scripts/restore.py $BackupZip
} else {
    & $PythonExe scripts/restore.py
}
