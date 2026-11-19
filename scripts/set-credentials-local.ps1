# Script seguro para configurar credenciales locales de Upload-Post y AutoClip
# Este script almacena tus claves en un archivo local .env protegido (no versionado en git)
# y opcionalmente en la configuración local de AutoClip.

param(
    [Parameter(Mandatory=$false)]
    [string]$UploadPostApiKey,

    [Parameter(Mandatory=$false)]
    [string]$UploadPostUser = "main",

    [Parameter(Mandatory=$false)]
    [string]$AutoclipApiKey
)

$ErrorActionPreference = "Stop"

Write-Host "=== Configuración Segura de Credenciales Locales para ClipFlow ===" -ForegroundColor Cyan

# 1. Solicitar Upload-Post API Key si no se pasó por parámetro
if (-not $UploadPostApiKey) {
    $secKey = Read-Host -Prompt "Introduce tu UPLOAD_POST_API_KEY (la entrada estará oculta)" -AsSecureString
    $BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secKey)
    $UploadPostApiKey = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)
}

if (-not $UploadPostApiKey) {
    Write-Host "No se proporcionó API Key de Upload-Post. Cancelando." -ForegroundColor Red
    exit 1
}

# 2. Escribir archivo .env local en la raíz del proyecto
$envPath = Join-Path $PSScriptRoot "..\.env"
$envContent = @"
# Credenciales locales de ClipFlow
UPLOAD_POST_API_KEY=$UploadPostApiKey
UPLOAD_POST_USER=$UploadPostUser
CLIPFLOW_DATA_DIR=C:/Users/PD2/Desktop/clips/data
CLIPFLOW_DATABASE_URL=sqlite:///C:/Users/PD2/Desktop/clips/data/clipflow.db
CLIPFLOW_TIMEZONE=America/Santo_Domingo
"@

if ($AutoclipApiKey) {
    $envContent += "`nAUTOCLIP_API_KEY=$AutoclipApiKey"
}

Set-Content -Path $envPath -Value $envContent -Encoding UTF8 -Force
Write-Host "✓ Archivo local .env generado en $envPath" -ForegroundColor Green

# 3. También guardar en la configuración nativa de AutoClip (%APPDATA%\AutoClip\upload_post.json)
$appdataDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::ApplicationData)
$autoClipConfigDir = Join-Path $appdataDir "AutoClip"
if (-not (Test-Path $autoClipConfigDir)) {
    New-Item -ItemType Directory -Path $autoClipConfigDir -Force | Out-Null
}
$autoClipConfigFile = Join-Path $autoClipConfigDir "upload_post.json"
$configData = @{
    api_key = $UploadPostApiKey
    user = $UploadPostUser
} | ConvertTo-Json

Set-Content -Path $autoClipConfigFile -Value $configData -Encoding UTF8 -Force
Write-Host "✓ Configuración guardada en $autoClipConfigFile" -ForegroundColor Green

# 4. Probar perfiles con clipflow accounts
Write-Host "`nProbando conexión con Upload-Post..." -ForegroundColor Cyan
& ".\.venv\Scripts\clipflow.exe" accounts

