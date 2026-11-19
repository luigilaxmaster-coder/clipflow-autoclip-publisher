# AGENTS.md — Reglas y Guía Permanente para Asistentes y Codex en ClipFlow

Este documento contiene las directrices obligatorias para cualquier modelo o agente de IA que trabaje en este repositorio (`autoclip-publisher`).

---

## 1. Principio Rector

- **Reutilización de AutoClip:** No reescribir la lógica de recorte, renderizado de video, motor de Studio ni la integración de Upload-Post. Reutilizar las funciones nativas en `backend/` de AutoClip.
- **Aislamiento:** La capa de orquestación reside en `src/clipflow/`. Mantener el código desacoplado y comprobable con tests unitarios e integrados.
- **Seguridad Primero:** Nunca almacenar, imprimir ni registrar claves secretas (`UPLOAD_POST_API_KEY`, `AUTOCLIP_API_KEY`) en el chat, logs ni archivos Git.

---

## 2. Convenciones de Código y Arquitectura

- **Python:** Compatible con Python 3.10+ (entorno oficial: CPython 3.11 en Windows x64).
- **Tipado Estricto:** Utilizar *type hints* en todas las funciones y clases.
- **Base de Datos:** Persistencia mediante SQLAlchemy 2.0 y SQLite local en `data/clipflow.db`. Todos los instantes temporales se almacenan en UTC (`utc_now()`).
- **Zona Horaria Local:** La zona de negocio es `America/Santo_Domingo` (UTC-4). Toda fecha introducida por el usuario se convierte a UTC antes de persistirse.
- **Idempotencia:** Cada trabajo de publicación debe tener una clave única (`idempotency_key`) para evitar publicaciones duplicadas en redes sociales ante caídas de red o reinicios de workers.

---

## 3. Protocolo de Publicación en Redes Sociales

- **Privacidad por Defecto:**
  - YouTube Shorts: Siempre `private` (o `unlisted` para revisión).
  - TikTok: Siempre `SELF_ONLY`.
  - La publicación en modo público solo se permite tras autorización manual explícita.
- **Confirmación Remota:** Una respuesta HTTP 200 de aceptación o la asignación de un `request_id` **no** significa que el video esté publicado. Se debe consultar el estado hasta que el proveedor confirme `completed` y entregue una URL real.
- **Manejo de Borradores de TikTok:** Si TikTok responde con envío a borrador o bandeja de entrada (`fallback_to_inbox`), registrar el estado como `ACTION_REQUIRED`, nunca como `PUBLISHED`.

---

## 4. Comandos de Verificación Obligatorios

Antes de dar por finalizada cualquier tarea o pull request, ejecutar la suite de pruebas completa:

```powershell
pytest -v
```

Todos los 23+ tests deben pasar sin advertencias críticas.
