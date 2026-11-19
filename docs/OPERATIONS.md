# Guía de Operaciones y Runbook de ClipFlow

## 1. Requisitos Previos

- **Sistema Operativo:** Windows 10/11 x64 (compatible con Linux/macOS).
- **Python:** 3.10 o superior (recomendado 3.11).
- **Herramientas del Sistema:** FFmpeg y FFprobe instalados y accesibles en PATH.
- **Gestor de paquetes:** `uv` (recomendado) o `pip`.

---

## 2. Instalación Rápida

Ejecutar desde PowerShell en la raíz del proyecto `autoclip-publisher`:

```powershell
# 1. Ejecutar el script automatizado de configuración
.\scripts\setup-windows.ps1

# 2. Verificar que todos los componentes estén operativos
clipflow doctor
```

---

## 3. Comandos Esenciales de Operación

### 3.1 Diagnóstico del Entorno
```powershell
clipflow doctor
```
Verifica Python, FFmpeg, Whisper local, conectividad a la base de datos y estado de Upload-Post.

### 3.2 Cuentas Conectadas en Upload-Post
```powershell
clipflow accounts
```
Consulta perfiles de Upload-Post, listando canales de YouTube y cuentas de TikTok activas o que requieran reconexión.

### 3.3 Creación y Procesamiento de Clips
```powershell
clipflow create --source "C:\Videos\conferencia.mp4" --platform youtube_shorts --platform tiktok --portrait-style podcast
```
Parámetros opcionales:
- `--srt <ruta.srt>`: Especifica subtítulos precisos ya sincronizados.
- `--template classic`: Estilo de edición (classic, editorial, street, news, cinema).
- `--max-clips 10`: Límite máximo de clips (hasta 10, sin relleno forzado).

### 3.4 Listar Proyectos y Ver Clips
```powershell
# Listar todos los proyectos
clipflow list

# Ver los clips generados, estado QA y borradores de un proyecto
clipflow clips <PROJECT_ID>
```

### 3.5 Aprobación de Borradores
```powershell
# Aprobar para YouTube
clipflow approve <DRAFT_ID_O_RENDER_ID> --platform youtube

# Aprobar para TikTok
clipflow approve <DRAFT_ID_O_RENDER_ID> --platform tiktok
```

### 3.6 Publicación Privada de Prueba
```powershell
clipflow publish <PROJECT_ID> --mode private-test
```
Despacha los clips aprobados asegurando que YouTube se suba como `private` y TikTok como `SELF_ONLY`.

### 3.7 Programación Horaria
```powershell
# Programar para las 9:00 AM hora local (Santo Domingo / America/Santo_Domingo)
clipflow schedule <PROJECT_ID> --date "2026-12-03T09:00:00-04:00"
```

### 3.8 Ejecución del Worker de Fondo
```powershell
# Iniciar worker persistente para procesar la cola y reconciliar estados
clipflow worker --interval 5
```

### 3.9 Iniciar el Panel Web de Administración
```powershell
clipflow web --port 8000
```
Abre en tu navegador `http://127.0.0.1:8000` para supervisar visualmente proyectos, previsualizar videos y aprobar publicaciones.

---

## 4. Respuestas y Códigos de Salida

- `0`: Operación exitosa.
- `1`: Fallo de ejecución de pipeline o error durante el renderizado.
- `2`: Parámetros inválidos, archivo no encontrado o rechazo de preflight.

---

## 5. Procedimiento ante Errores y Recuperación

1. **Error HTTP 429 (Límite de tasa en Upload-Post):**
   El worker automáticamente clasifica el trabajo como `RETRYABLE_ERROR` y aplica retroceso exponencial con jitter (5s, 10s, 20s) hasta alcanzar el límite de reintentos.
2. **Caída del proceso o reinicio de máquina:**
   Al reiniciar `clipflow worker`, la base de datos preserva los estados; los trabajos en `UPLOADING` o `REMOTE_PROCESSING` se reconcilian mediante su `remote_request_id` sin duplicar la subida del video.
3. **Rechazo en TikTok por cuenta desvinculada:**
   Si una plataforma falla, el trabajo se marca `ACTION_REQUIRED`. El éxito en YouTube se preserva intacto y no se reintenta innecesariamente.
