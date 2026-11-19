# ClipFlow - Iniciar Worker de Publicación en Segundo Plano
$ErrorActionPreference = "Continue"

Write-Host "Iniciando worker persistente de publicación de ClipFlow..." -ForegroundColor Cyan

if (Test-Path "..\.venv\Scripts\clipflow.exe") {
    & "..\.venv\Scripts\clipflow.exe" worker --interval 5
} elseif (Test-Path ".\.venv\Scripts\clipflow.exe") {
    & ".\.venv\Scripts\clipflow.exe" worker --interval 5
} else {
    & clipflow worker --interval 5
}
