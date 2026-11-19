# Políticas de Seguridad de ClipFlow

## 1. Gestión Segura de Credenciales y Secretos

- **Prohibición de Secretos en Repositorio:** Ninguna clave de API (`AUTOCLIP_API_KEY`, `UPLOAD_POST_API_KEY`, tokens de sesión) se almacena en el control de versiones ni en archivos Git.
- **Aislamiento de Entornos:**
  - `AUTOCLIP_API_KEY`: Clave de modelo de lenguaje. Solo accesible por el componente de análisis.
  - `UPLOAD_POST_API_KEY`: Clave de publicación en redes sociales. Solo accesible por el módulo `clipflow.publishing.upload_post`.
- **Almacenamiento Local:** Los archivos de credenciales locales (`upload_post.json`) se restringen mediante permisos de archivo del sistema operativo (0600 en sistemas POSIX / ACL restringida en Windows).
- **Sanitización de Logs:** Los identificadores de token se enmascaran automáticamente en registros de terminal y archivos de log (`sk-1234...abcd`).

---

## 2. Protección contra Ataques de Red (SSRF)

El servicio de ingesta implementa validación estricta de URLs remotas:
1. Solo se admiten conexiones HTTPS con certificados TLS válidos.
2. Solo se permiten dominios de plataformas reconocidas (`youtube.com`, `youtu.be`, `bilibili.com`, `b23.tv`).
3. Se realiza resolución DNS previa para bloquear peticiones dirigidas a:
   - Direcciones de bucle invertido (`localhost`, `127.0.0.1`, `::1`).
   - Redes privadas locales (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`).
   - Direcciones de enlace local y metadatos de proveedores en la nube (`169.254.169.254`).

---

## 3. Políticas de Publicación y Aprobación

- **Modo Predeterminado Seguro:** `draft_only`. El sistema no publicará ningún video de forma automática sin una aprobación manual previa o la configuración explícita y consciente del modo `auto_approved`.
- **Visibilidad Restringida:** En pruebas de publicación, YouTube se fija obligatoriamente en `private` (o `unlisted`) y TikTok en `SELF_ONLY`. El cambio a visibilidad pública exige confirmación explícita.
- **Trazabilidad Inmutable:** Cada cambio de visibilidad, aprobación manual o despacho a publicación queda registrado en la tabla `audit_events` con el identificador del actor, la fecha UTC y la carga de datos previa y posterior.
- **Idempotencia Criptográfica:** Cada envío cuenta con un encabezado `Idempotency-Key` derivado del hash del contenido y la configuración para evitar subidas dobles accidentales ante problemas de red.
