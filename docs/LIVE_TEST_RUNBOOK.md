# Guía de Ejecución de la Prueba Real en YouTube (FASE 2 y FASE 3)

Este documento detalla el procedimiento exacto para activar la prueba real en YouTube una vez que introduzcas tu API Key de Upload-Post.

---

## Paso 1: Configurar tu API Key de Upload-Post

Ejecuta en PowerShell dentro de `C:\Users\PD2\Desktop\clips`:

```powershell
.\scripts\set-credentials-local.ps1
```

El script te solicitará tu `UPLOAD_POST_API_KEY` de forma segura (con caracteres ocultos), guardará la clave localmente en `.env` (no versionado en git) y en `%APPDATA%\AutoClip\upload_post.json`.

---

## Paso 2: Verificar Conexión de tu Canal de YouTube

Comprueba que Upload-Post reconozca tu cuenta y canal:

```powershell
.\.venv\Scripts\activate
clipflow accounts
```

Deberás ver tu usuario (ej. `main`) con la plataforma `youtube` en la lista de plataformas conectadas.

---

## Paso 3: Ejecutar la Subida Privada Real (FASE 3)

Ejecuta el script dedicado de prueba real:

```powershell
python scripts\test_live_youtube.py
```

El script realizará:
1. Generación de un clip de 5 segundos con FFmpeg (1080x1920 vertical 9:16 con audio AAC).
2. Validación de conformidad técnica con FFprobe.
3. Envío seguro a YouTube con `privacyStatus=private`, `async_upload=true` y un `Idempotency-Key` único.
4. Sondeo automático cada 10 segundos hasta confirmar el resultado definitivo.
5. Verificación de que YouTube no aparezca como `skipped` y entrega del ID y URL privada.

