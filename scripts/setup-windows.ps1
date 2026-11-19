# ClipFlow - Script de Configuración Inicial en Windows
$ErrorActionPreference = "Stop"

Write-Host "=== Instalación y Configuración de ClipFlow en Windows ===" -ForegroundColor Cyan

# 1. Crear entorno virtual con Python 3.11 si no existe
if (-not (Test-Path ".venv")) {
    Write-Host "Creando entorno virtual con uv (Python 3.11)..."
    uv venv --python 3.11 .venv
}

# 2. Activar entorno virtual
$activateScript = ".\.venv\Scripts\Activate.ps1"
if (Test-Path $activateScript) {
    . $activateScript
}

# 3. Instalar dependencias upstream de AutoClip
$upstreamReq = if (Test-Path ".\autoclip-upstream\requirements.txt") { ".\autoclip-upstream" } else { "..\autoclip-upstream" }
if (Test-Path "$upstreamReq\requirements.txt") {
    Write-Host "Instalando dependencias de AutoClip upstream desde $upstreamReq..."
    uv pip install -r "$upstreamReq\requirements.txt"
    uv pip install --no-deps -e "$upstreamReq"
}

# 4. Instalar faster-whisper para transcripción local
Write-Host "Instalando faster-whisper..."
uv pip install faster-whisper

# 5. Instalar clipflow
Write-Host "Instalando clipflow..."
uv pip install -e .

Write-Host "=== Instalación completada con éxito ===" -ForegroundColor Green
& clipflow doctor
