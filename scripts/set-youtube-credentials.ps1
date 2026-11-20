<#
.SYNOPSIS
    Configura credenciales oficiales de YouTube OAuth 2.0 de forma local y segura.
.DESCRIPTION
    Solicita interactivamente Client ID y Client Secret (y opcionalmente tokens separados).
    Nunca inserta secretos en código Python ni en argumentos de línea de comandos visibles.
    Envía las credenciales exclusivamente mediante stdin hacia clipflow.connections.cli_store
    y limpia los buffers de memoria (ZeroFreeBSTR) al finalizar.
#>

[CmdletBinding()]
param(
    [string]$AccountName = "Canal del Propietario"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CONFIGURACION SEGURA DE YOUTUBE OAUTH 2.0" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Las credenciales se cifran localmente con Fernet 256-bit." -ForegroundColor Gray
Write-Host "Recomendado: introduce solo Client ID y Client Secret, y luego" -ForegroundColor Gray
Write-Host "completa la autorizacion interactiva en http://127.0.0.1:8000/connections" -ForegroundColor Gray
Write-Host ""

function ConvertFrom-SecureStringSafe {
    param([System.Security.SecureString]$Secure)
    if ($null -eq $Secure -or $Secure.Length -eq 0) {
        return ""
    }
    $ptr = [System.IntPtr]::Zero
    try {
        $ptr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
        return [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($ptr)
    } finally {
        if ($ptr -ne [System.IntPtr]::Zero) {
            [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

$ClientId = ""
$ClientSecret = ""
$RefreshToken = ""
$AccessToken = ""
$PayloadJson = ""

try {
    $ClientId = (Read-Host "Introduce tu YOUTUBE_CLIENT_ID").Trim()
    $ClientSecretSecure = Read-Host "Introduce tu YOUTUBE_CLIENT_SECRET" -AsSecureString
    $ClientSecret = (ConvertFrom-SecureStringSafe -Secure $ClientSecretSecure).Trim()

    if (-not $ClientId -or -not $ClientSecret) {
        Write-Host "[!] Error: Client ID y Client Secret son obligatorios." -ForegroundColor Red
        exit 1
    }

    Write-Host ""
    Write-Host "Opcional (deja vacio para usar el flujo OAuth interactivo en el navegador):" -ForegroundColor DarkGray
    $RefreshTokenSecure = Read-Host "Introduce tu YOUTUBE_REFRESH_TOKEN (opcional)" -AsSecureString
    $RefreshToken = (ConvertFrom-SecureStringSafe -Secure $RefreshTokenSecure).Trim()

    $AccessTokenSecure = Read-Host "Introduce tu YOUTUBE_ACCESS_TOKEN (opcional, distinto al refresh_token)" -AsSecureString
    $AccessToken = (ConvertFrom-SecureStringSafe -Secure $AccessTokenSecure).Trim()

    $CredsHash = @{
        client_id     = $ClientId
        client_secret = $ClientSecret
    }
    if ($RefreshToken) {
        $CredsHash["refresh_token"] = $RefreshToken
    }
    if ($AccessToken) {
        $CredsHash["access_token"] = $AccessToken
    }

    $PayloadJson = $CredsHash | ConvertTo-Json -Compress
    $PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"
    if (-not (Test-Path $PythonExe)) {
        Write-Host "[!] Error: No se encontro el entorno virtual .venv." -ForegroundColor Red
        exit 1
    }

    # Paso seguro por stdin: ningun secreto aparece en argumentos de proceso ni codigo interpolado
    $PayloadJson | & $PythonExe -m clipflow.connections.cli_store --provider youtube --account-name $AccountName
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }

    Write-Host ""
    Write-Host "[*] Siguiente paso: abre http://127.0.0.1:8000/connections y pulsa 'Autorizar con Google OAuth'." -ForegroundColor Green
} finally {
    # Limpieza explicita de variables sensibles y recoleccion de memoria
    $ClientId = $null
    $ClientSecret = $null
    $RefreshToken = $null
    $AccessToken = $null
    $PayloadJson = $null
    if ($null -ne $CredsHash) { $CredsHash.Clear() }
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
}
