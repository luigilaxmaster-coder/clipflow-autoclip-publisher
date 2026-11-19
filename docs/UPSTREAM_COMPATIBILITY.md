# Compatibilidad con AutoClip Upstream

**Versión auditada de AutoClip:** 1.5.8 / 1.5.9 RC  
**Commit upstream:** `cd8e6bed18d87a81273d48bcd165751708ac715c` (Tue Nov 3 2026)  
**Fecha de auditoría:** 19 de noviembre de 2026  
**Entorno de ejecución:** Windows 11 Pro x64, Python 3.11.15, FFmpeg 9.0.1  

---

## 1. Mapeo de Capacidades Upstream

| Capacidad | Módulo Upstream | Estado y Comportamiento Verificado |
|---|---|---|
| CLI `produce` | `backend/cli.py:cmd_produce` | Invoca `quick_output_runner.start` y `wait`. Genera variantes de salida Studio. |
| CLI `outputs` | `backend/cli.py:cmd_outputs` | Invoca `quick_output_runner.status(project_id, export_kits=True)`. Devuelve salidas y kits ZIP. |
| CLI `publish` | `backend/cli.py:cmd_publish` | Soporta `--clip`, `--platform`, `--user`, `--extra`. Consulta perfiles con `--list-profiles`. |
| MCP Server | `backend/mcp_server.py` | Expone herramientas `start_quick_output`, `get_quick_output_status`, `publish_clip`, `get_publish_status`, `list_publish_profiles`. |
| Motor Studio | `backend/services/quick_output_runner.py` | Orquesta segmentación, transcriptor, empaquetado y renderizado 9:16 (`output/studio/{job_id}.mp4`). |
| Upload-Post Publisher | `backend/services/upload_post_publisher.py` | Gestiona subida multipart a `https://api.upload-post.com/api/upload` con `async_upload=true`. |
| Variantes Studio | `backend/services/studio/publishing.py` | `output_variant_meta` resuelve variantes terminadas sin re-renderizado. |
| Estrategias de Plataforma | `backend/services/platform_strategy.py` | Define `youtube_shorts`, `tiktok`, `douyin`, `bilibili`, etc. Mapea transporte `youtube` y `tiktok`. |

---

## 2. Puente Crítico: Output Variants de Studio → Publicación

En AutoClip 1.5+, la generación con `produce` crea variantes de salida en Studio:
1. Cada variante en `output_variants` posee:
   - `id`: Identificador de la variante (ej. `variant-xxxx`).
   - `strategy_id`: Plataforma de edición (`youtube_shorts`, `tiktok`).
   - `render_job_id`: Hash hexadecimal de 32 caracteres del trabajo de render.
   - `video_path`: Archivo renderizado final inmutable ubicado en `<project_dir>/output/studio/{job_id}.mp4`.
   - `post`: Diccionario con `title`, `description`, `tags`.
   - `cover_path`: Portada generada.
   - `kit_path`: Archivo ZIP exportado.

2. **Resolución en el Publicador:**
   - En `backend/services/upload_post_publisher.py`:
     ```python
     if req.output_variant_id:
         clip = output_variant_meta(req.project_id, req.output_variant_id)
         blocked = incompatible_transport_platforms(clip['strategy_id'], platforms)
         export = {'ok': True, 'path': clip['video_path'], 'preset': clip['strategy_id'], 'warnings': clip['warnings']}
     ```
   - Al proveer `output_variant_id`, se reutiliza directamente el archivo MP4 ya renderizado (`clip['video_path']`), evitando re-exportaciones y dobles codificaciones.
   - Alternativamente, si se utiliza el ID de clip con prefijo `studio-{job_id}`, `load_clip_meta` en `backend/services/publish_export.py` lo resuelve idénticamente a través de `export_meta`.

3. **Mapeo de Plataformas:**
   - **Generación:** Se solicitan estrategias 9:16 verticales `youtube_shorts` y `tiktok`.
   - **Publicación:** Los identificadores de transporte para Upload-Post son `youtube` y `tiktok`.
   - `incompatible_transport_platforms`: Como ambas estrategias tienen relación de aspecto `portrait` (1080x1920), son totalmente compatibles con los destinos verticales de Upload-Post.

---

## 3. Manejo Seguro de Secretos

- `AUTOCLIP_API_KEY`: Clave de modelo de lenguaje / ASR. Solo consumida mediante variable de entorno o configuración local en `cli-settings.json`.
- `UPLOAD_POST_API_KEY`: Clave de publicación en Upload-Post. Se almacena localmente en `<data_dir>/upload_post.json` con permisos restringidos o mediante variable de entorno `UPLOAD_POST_API_KEY`.
- `UPLOAD_POST_USER`: Nombre del perfil de usuario en Upload-Post (ej. `main`).
- El orquestador `clipflow` no expone claves en logs, respuestas de terminal ni repositorios Git.
