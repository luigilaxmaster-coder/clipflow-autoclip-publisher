# ClipFlow — AutoClip Publisher

> **Sistema de orquestación, validación multimedia y publicación automatizada de clips verticales (9:16) para YouTube Shorts y TikTok, construido sobre el motor de [AutoClip](https://github.com/zhouxiaoka/autoclip).**

---

## 🚀 Características Principales

1. **Motor de Corte Nativo AutoClip:** Utiliza el análisis semántico de Studio, transcripción ASR local con Whisper y renderizado de video 9:16 con subtítulos dinámicos integrados.
2. **Control de Calidad Estricto (MediaQA):** Valida cada MP4 generado con `ffprobe` (duración, ratio 9:16, códecs de video H.264 y audio AAC, verificación de pistas y ausencia de corrupción).
3. **Metadatos Adaptados por Plataforma:**
   - **YouTube Shorts:** Título con límite de 100 caracteres, descripción estructurada con `#Shorts` y hashtags de nicho.
   - **TikTok:** Copy conciso optimizado para feed vertical con hashtags virales (`#fyp`, `#parati`).
4. **Publicación y Programación Segura con Upload-Post:**
   - Soporte directo de variantes de salida Studio sin re-renderizado (`output_variant_id`).
   - Privacidad estricta por defecto: YouTube en `private` y TikTok en `SELF_ONLY`.
   - Idempotencia garantizada por hash para evitar duplicados en reinicios o caídas.
   - Reconciliación asíncrona real y seguimiento de estados remotos.
5. **Cola Durable & Recuperación de Errores:**
   - Worker persistente con SQLite local y transacciones atómicas.
   - Reintentos exponenciales con jitter para límites de tasa (HTTP 429) y errores transitorios.
   - Preservación de éxito independiente si una plataforma falla.
6. **Panel Web de Administración:**
   - Interfaz visual moderna (FastAPI + Tailwind CSS) para monitorear proyectos, ver previsualizaciones de video en formato 9:16, revisar métricas de QA y aprobar publicaciones.

---

## 🛠️ Instalación en Windows

### Requisitos Previos
- Windows 10/11 x64
- Python 3.10 o superior (recomendado CPython 3.11)
- FFmpeg y FFprobe en el PATH del sistema
- Gestor `uv` (recomendado)

### Paso 1: Configurar el Entorno
Desde PowerShell:
```powershell
# Ejecutar script de configuración automatizada
.\scripts\setup-windows.ps1
```

O manualmente:
```powershell
uv venv --python 3.11 .venv
.\.venv\Scripts\Activate.ps1
uv pip install -r ..\autoclip-upstream\requirements.txt
uv pip install --no-deps -e ..\autoclip-upstream
uv pip install faster-whisper
uv pip install -e .
```

### Paso 2: Verificar el Entorno
```powershell
clipflow doctor
```

---

## 📖 Uso de la CLI `clipflow`

### Diagnóstico de Sistema
```powershell
clipflow doctor
```

### Listar Perfiles de Upload-Post
```powershell
clipflow accounts
```

### Procesar un Video y Generar Clips
```powershell
clipflow create --source "C:\Videos\entrevista.mp4" --platform youtube_shorts --platform tiktok --portrait-style podcast
```
*Opciones adicionales:*
- `--srt "C:\Videos\entrevista.srt"`: Subtítulos manuales pre-sincronizados.
- `--template classic`: Estilo de maquetación (classic, editorial, street, news, cinema).
- `--max-clips 10`: Número máximo de clips a seleccionar.

### Consultar Proyectos y Clips
```powershell
# Listar proyectos
clipflow list

# Ver clips y estado QA de un proyecto
clipflow clips <PROJECT_ID>

# Ver resumen completo en JSON
clipflow status <PROJECT_ID>
```

### Aprobación y Publicación Privada
```powershell
# Aprobar borrador para YouTube
clipflow approve <DRAFT_ID> --platform youtube

# Despachar publicación privada de prueba
clipflow publish <PROJECT_ID> --mode private-test
```

### Programar Publicación en Fecha Futura
```powershell
clipflow schedule <PROJECT_ID> --date "2026-12-03T09:00:00-04:00"
```

### Iniciar Worker Persistente
```powershell
clipflow worker --interval 5
```

### Iniciar Panel Web de Administración
```powershell
clipflow web --port 8000
```
Accede desde tu navegador a [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## 🧪 Pruebas Automatizadas

Ejecutar la suite completa de pruebas unitarias, de integración y end-to-end:

```powershell
pytest -v
```

---

## 📂 Estructura del Repositorio

```text
autoclip-publisher/
├── AGENTS.md                          # Reglas para agentes IA y Codex
├── README.md                          # Documentación del proyecto
├── pyproject.toml                     # Definición del paquete y dependencias
├── .env.example                       # Plantilla de variables de entorno seguras
├── config/
│   └── settings.example.yaml          # Configuración YAML del orquestador
├── src/clipflow/
│   ├── cli.py                         # CLI humana y automatizada (clipflow)
│   ├── config.py                      # Carga y validación de configuración
│   ├── models.py                      # Modelos SQLAlchemy para SQLite
│   ├── db.py                          # Conexión, transacciones y auditoría
│   ├── ingestion.py                   # Ingesta, validación SSRF y hash SHA-256
│   ├── autoclip_adapter.py            # Adaptador nativo AutoClip Studio
│   ├── selector.py                    # Algoritmo de ranking y deduplicación
│   ├── media_qa.py                    # Validador estricto FFprobe (9:16, codecs)
│   ├── metadata.py                    # Generación de títulos y hashtags por red
│   ├── publishing/
│   │   ├── base.py                    # Interfaz abstracta de publicación
│   │   ├── upload_post.py             # Cliente Upload-Post con variante Studio
│   │   └── reconcile.py               # Reconciliación de estados asíncronos
│   ├── queue.py                       # Worker durable y control de reintentos
│   ├── scheduler.py                   # Conversión horaria Santo Domingo / UTC
│   ├── approvals.py                   # Aprobación manual y políticas automáticas
│   ├── reporting.py                   # Informes y exportación JSON
│   └── web/                           # Panel Web de Administración (FastAPI)
│       ├── app.py
│       └── templates/
├── scripts/
│   ├── check-environment.ps1          # Diagnóstico rápido de Windows
│   ├── setup-windows.ps1              # Instalación en un solo paso
│   ├── start-worker.ps1               # Inicio de worker persistente
│   └── set-credentials-local.ps1      # Configuración segura de API Keys
├── tests/
│   ├── fixtures/                      # Videos de prueba reales 9:16 y 16:9
│   ├── test_ingestion.py
│   ├── test_autoclip_adapter.py
│   ├── test_media_qa.py
│   ├── test_publish_mock.py
│   ├── test_idempotency.py
│   ├── test_scheduler.py
│   ├── test_recovery.py
│   ├── test_selector_and_metadata.py
│   ├── test_web_api.py
│   └── test_e2e_flow.py
└── docs/
    ├── ARCHITECTURE.md                # Arquitectura técnica y diagramas
    ├── OPERATIONS.md                  # Runbook de operaciones
    ├── SECURITY.md                    # Políticas de seguridad y anti-SSRF
    ├── ACCEPTANCE.md                  # Matriz de Gates de aceptación
    └── UPSTREAM_COMPATIBILITY.md      # Análisis de compatibilidad con AutoClip
```
