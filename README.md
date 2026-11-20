# AutoClip Publisher — Personal Edition

> **Plataforma privada y personal de publicación, programación y gestión multicanal para redes sociales (YouTube Shorts, TikTok, Instagram Reels, X, Threads, LinkedIn, Bluesky, Telegram y más), integrada directamente sobre el motor de [AutoClip](https://github.com/zhouxiaoka/autoclip) y compatible con Postiz autohospedado.**

---

## 🎯 Enfoque y Filosofía de Diseño

- **100% Personal & Privado:** Diseñado exclusivamente para el uso personal de un propietario. Sin suscripciones, pagos, facturación, Stripe ni funciones multiempresa.
- **Soberanía de Datos & Cifrado Local:** Todas las credenciales, claves API y tokens de acceso OAuth se almacenan cifrados localmente mediante **Fernet (AES-128-CBC / SHA-256 HMAC)** con clave persistida en `data/.secret.key`.
- **Motor Híbrido Desacoplado:** Soporta publicación nativa directa (Google OAuth 2.0 / YouTube Data API v3), integración con el motor autohospedado **Postiz (AGPL-3.0)** y retrocompatibilidad con Upload-Post.
- **Procesamiento Local FFmpeg:** Control estricto de calidad vertical 9:16 con `ffprobe` y generación de miniaturas locales sin depender de servicios SaaS externos.
- **Transparencia y Veracidad:** No se inventan métricas ni se simulan aprobaciones. Cada plataforma refleja su estado real de autorización y sus límites oficiales documentados.

---

## 🖥️ Las 10 Secciones del Dashboard

El panel de administración cuenta con una interfaz moderna y responsiva construida con FastAPI, Jinja2 y Tailwind CSS, accesible en `http://127.0.0.1:8000`:

1. **Overview (`/`):** Métricas generales, estado del motor local de cortes, proyectos recientes de AutoClip y flujo de publicaciones.
2. **Connected Apps (`/connections`):** Centro de gestión para los **18 proveedores integrados** (14 redes sociales + 4 sistemas de almacenamiento en la nube y disco local) con pruebas de diagnóstico en vivo.
3. **Publisher (`/publisher`):** Creación multired con previsualización vertical 9:16, optimización de metadatos (límite estricto de 100 caracteres para YouTube Shorts), selección de privacidad y programación.
4. **Calendar (`/calendar`):** Calendario editorial visual en zona horaria `America/Santo_Domingo` (UTC-4), reprogramación y cancelación.
5. **Media Library (`/media-library`):** Biblioteca central que indexa automáticamente los renders terminados de AutoClip sin duplicar espacio en disco, con miniaturas FFmpeg.
6. **Analytics (`/analytics`):** Estadísticas reales recuperadas mediante llamadas oficiales (vistas, me gusta, comentarios, engagement) e integración de la *Fórmula Ganadora* de AutoClip.
7. **Inbox (`/inbox`):** Lectura de comentarios y respuestas oficiales directas (YouTube Data API v3), más un motor de auto-respuestas con cooldown de seguridad por usuario y filtros por palabra clave.
8. **Automations (`/automations`):** Reglas para auto-aprobar clips que superen MediaQA, distribución en mejores horarios (10:00 AM, 3:00 PM, 8:00 PM) y generación de títulos de alta retención.
9. **Queue & Jobs (`/queue`):** Monitor de cola persistente en SQLite con **10 estados explícitos**, reintentos con backoff exponencial, jitter y reconciliación anti-duplicados.
10. **Settings (`/settings`):** Configuración de token maestro de propietario, estado del cifrado Fernet, creación y restauración de copias de seguridad ZIP verificadas y diagnósticos de salud del sistema.

---

## 🔌 Matriz de los 18 Conectores

| Proveedor | Tipo | Protocolo / Autenticación | Capacidades Principales |
|---|---|---|---|
| **YouTube** | Social | Google OAuth 2.0 (v3) | Shorts 9:16, Subida resumible, Miniaturas, Comentarios, Analíticas (Nov 2026: público/oculto permitido en apps dev). |
| **TikTok** | Social | Content Posting API | Direct Post, Videos verticales, Borrador en bandeja de entrada (`SELF_ONLY` en apps personales). |
| **Instagram** | Social | Meta Graph API | Reels 9:16, Carruseles, Comentarios (Requiere cuenta Profesional vinculada a Página). |
| **Facebook Pages** | Social | Meta Graph API | Publicación en Páginas, Reels, Métricas de interacción. |
| **Threads** | Social | Threads API | Videos cortos, Publicaciones de texto, Respuestas. |
| **X (Twitter)** | Social | OAuth 2.0 / API v2 | Publicación de clips, Tweets (Límite 280 caracteres, cuota Free Tier). |
| **LinkedIn** | Social | OAuth 2.0 / Community API | Publicación en perfiles personales y páginas corporativas. |
| **Pinterest** | Social | Pinterest API v5 | Idea Pins, Video Pins con selección de tablero (`board_id`). |
| **Bluesky** | Social | AT Protocol | Publicación descentralizada mediante App Password segura. |
| **Reddit** | Social | OAuth 2.0 Script | Envío de videos a subreddits propios o autorizados. |
| **Telegram** | Social | Bot API | Envío de videos verticales a canales y grupos mediante Bot Token. |
| **Discord** | Social | Webhooks / Bot API | Publicación automatizada en canales de Discord. |
| **WordPress** | Social | REST API | Publicación de entradas y multimedia con Application Passwords. |
| **Google Business** | Social | Google Business Profile API | Publicaciones locales y actualizaciones multimedia para empresas. |
| **Google Drive** | Ingesta | Google Drive API v3 | Ingesta automática de videos largos y podcasts almacenados en la nube. |
| **OneDrive** | Ingesta | Microsoft Graph API | Ingesta desde carpetas de OneDrive personal o empresarial. |
| **Dropbox** | Ingesta | Dropbox API | Descarga e importación directa de grabaciones. |
| **Carpeta Local** | Ingesta | Local Storage | Monitoreo directo en disco o red LAN de archivos `.mp4`, `.mov`, `.mkv`. |

---

## ⚙️ Máquina de Estados de Publicación

Cada trabajo transita por estados persistentes explícitos para garantizar idempotencia y tolerancia a fallos:

```
[DRAFT] ──> [READY] ──> [SCHEDULED] ──> [QUEUED] ──> [UPLOADING] ──> [PROCESSING] ──> [PUBLISHED]
                                            │                                │
                                            ▼                                ▼
                                  [RETRYABLE_ERROR]                  [REQUIRES_ACTION]
                                            │                                │
                                            ▼                                ▼
                                    [Reintento Backoff]              [Pausa / Token Expirado]
```

- **Prevención de Duplicados:** Cada trabajo genera una clave única determinista (`idempotency_key`). Antes de reintentar una subida incierta, el worker consulta el estado remoto.
- **Pausa Automática:** Ante errores HTTP 401 (`unauthorized`, `token_expired`), el conector pasa a `EXPIRED` y los trabajos pendientes para ese canal se pausan en `REQUIRES_ACTION` para evitar consumir cuotas inútilmente.

---

## 🚀 Inicio y Operación en Windows

### 1. Iniciar el Dashboard y el Worker
Desde PowerShell:
```powershell
.\scripts\run-publisher.ps1
```
Abre tu navegador en: [http://127.0.0.1:8000](http://127.0.0.1:8000)

### 2. Configurar Conexión Oficial de YouTube (Seguro y Local)
```powershell
.\scripts\set-youtube-credentials.ps1
```
Solicita interactivamente el Client ID, Client Secret y Refresh Token de forma segura (sin eco en pantalla ni guardado en texto plano).

### 3. Ejecutar Worker Persistente en Segundo Plano
```powershell
.\scripts\run-publisher.ps1 -WorkerOnly
```

### 4. Crear Copia de Seguridad Verificada
```powershell
.\scripts\backup-database.ps1
```
Genera un archivo `.zip` en `data/backups/` con la base de datos SQLite y las claves de cifrado.

### 5. Restaurar Copia de Seguridad
```powershell
.\scripts\restore-database.ps1
```
Verifica la integridad física del ZIP y de SQLite (`PRAGMA integrity_check`) antes de aplicar la restauración, preservando un respaldo previo de rollback.

---

## 🐳 Despliegue Opcional con Docker Compose (Postiz + AutoClip)

Si dispones de Docker Desktop en Windows o despliegas en un servidor local Linux:
```powershell
# Validar configuración
docker compose -f docker-compose.publisher.yml config

# Iniciar Postiz, PostgreSQL, Redis, Temporal y AutoClip Publisher
docker compose -f docker-compose.publisher.yml up -d --build
```
- **Postiz UI:** `http://localhost:4007`
- **AutoClip Publisher:** `http://localhost:8000`
- **Temporal Dashboard:** `http://localhost:8088`

---

## 🧪 Pruebas Automatizadas

Ejecutar la suite completa de 39 pruebas unitarias, de integración y E2E:
```powershell
.\.venv\Scripts\python.exe -m pytest -v
```

---

## 🔒 Seguridad y Privacidad

- **Sin Telemetría Externa:** Postiz tiene la telemetría desactivada (`DISABLE_TELEMETRY: "true"`).
- **Sin Exposición Pública:** Diseñado para ejecutarse en `127.0.0.1`. Si se expone en red local o mediante túnel (Cloudflare / Tailscale), activar HTTPS y configurar un `OWNER_TOKEN` robusto.
- **Políticas de Privacidad por Defecto:**
  - YouTube Shorts: Siempre `private` en pruebas.
  - TikTok: Siempre `SELF_ONLY`.
