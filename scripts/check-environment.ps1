# ClipFlow - Diagnóstico y verificación del entorno en Windows
$ErrorActionPreference = "Continue"

Write-Host "=== Verificación del Entorno Windows para ClipFlow ===" -ForegroundColor Cyan

# 1. Python
$pythonVer = & python --version 2>&1
Write-Host "Python: $pythonVer"

# 2. FFmpeg / FFprobe
$ffmpegVer = & ffmpeg -version 2>&1 | Select-Object -First 1
Write-Host "FFmpeg: $ffmpegVer"
$ffprobeVer = & ffprobe -version 2>&1 | Select-Object -First 1
Write-Host "FFprobe: $ffprobeVer"

# 3. Virtualenv y Clipflow
if (Test-Path "..\.venv\Scripts\clipflow.exe") {
    & "..\.venv\Scripts\clipflow.exe" doctor
} elseif (Test-Path ".\.venv\Scripts\clipflow.exe") {
    & ".\.venv\Scripts\clipflow.exe" doctor
} else {
    Write-Host "clipflow no encontrado en PATH activo. Ejecute setup-windows.ps1 primero." -ForegroundColor Yellow
}
