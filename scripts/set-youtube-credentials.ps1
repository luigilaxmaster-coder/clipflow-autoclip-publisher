<#
.SYNOPSIS
    Configura credenciales oficiales de YouTube de forma local y segura sin exponerlas en logs.
.DESCRIPTION
    Solicita interactivamente el Client ID, Client Secret y Token de Google/YouTube,
    los cifra con Fernet y los almacena directamente en la base de datos de AutoClip Publisher.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
Set-Location $ProjectDir

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "  CONFIGURACION SEGURA DE YOUTUBE OAUTH 2.0" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Las credenciales seran cifradas localmente con Fernet 256-bit." -ForegroundColor Gray
Write-Host "Nunca seran registradas en archivos Git ni logs." -ForegroundColor Gray
Write-Host ""

$ClientId = Read-Host "Introduce tu YOUTUBE_CLIENT_ID"
$ClientSecretSecure = Read-Host "Introduce tu YOUTUBE_CLIENT_SECRET" -AsSecureString
$BSTR = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ClientSecretSecure)
$ClientSecret = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR)

$RefreshTokenSecure = Read-Host "Introduce tu YOUTUBE_REFRESH_TOKEN (o Access Token)" -AsSecureString
$BSTR2 = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($RefreshTokenSecure)
$RefreshToken = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto($BSTR2)

if (-not $ClientId -or -not $ClientSecret) {
    Write-Host "[!] Error: Client ID y Client Secret son obligatorios." -ForegroundColor Red
    exit 1
}

$PythonExe = Join-Path $ProjectDir ".venv\Scripts\python.exe"

$ScriptCode = @"
import sys
from clipflow.db import init_db, get_session
from clipflow.connections.service import ConnectionService

init_db()
session = get_session()
service = ConnectionService(session)

creds = {
    'client_id': '$ClientId',
    'client_secret': '$ClientSecret',
    'refresh_token': '$RefreshToken',
    'access_token': '$RefreshToken',
}

res = service.save_connection(
    provider_id='youtube',
    account_name='Canal de Propietario',
    credentials=creds,
    test_now=True
)

print(f\"Resultado del diagnostico: {res['message']} (Estado: {res['status']})\")
if res['ok']:
    print(\"[+] YouTube conectado y verificado exitosamente!\")
else:
    print(\"[*] Credenciales cifradas y almacenadas. Se requiere verificacion OAuth.\")
"@

& $PythonExe -c $ScriptCode
