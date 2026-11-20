# Guía de Obtención de Credenciales por Proveedor
**AutoClip Publisher — Personal Edition**

Este documento detalla paso a paso cómo obtener las credenciales oficiales de cada uno de los 18 proveedores integrados en la plataforma, sin intermediarios comerciales y manteniendo el control de tus datos.

---

## 1. YouTube (Google Data API v3 & Google OAuth 2.0)
* **Tipo de Autenticación:** OAuth 2.0
* **Cuota inicial:** 100 llamadas diarias a `videos.insert` (1,600 unidades por video).
* **Novedad Noviembre 2026:** Google eliminó el bloqueo obligatorio de videos privados para proyectos personales no verificados. Los videos pueden publicarse como `public`, `unlisted` o `private`.
* **Pasos:**
  1. Ingresa a [Google Cloud Console](https://console.cloud.google.com/).
  2. Crea un proyecto nuevo: `AutoClip-Personal-Publisher`.
  3. En **APIs y Servicios > Biblioteca**, busca y habilita **YouTube Data API v3**.
  4. En **Pantalla de consentimiento de OAuth**:
     - Tipo de usuario: **Externo**.
     - Agrega los alcances (scopes):
       - `https://www.googleapis.com/auth/youtube.upload`
       - `https://www.googleapis.com/auth/youtube.readonly`
     - En **Usuarios de prueba**, añade tu propia dirección de correo de Google.
  5. En **Credenciales > Crear Credenciales > ID de cliente de OAuth**:
     - Tipo de aplicación: **Aplicación web**.
     - URI de redireccionamiento autorizados: `http://localhost:8000/api/connections/youtube/callback`.
  6. Copia el `Client ID` y `Client Secret` en la sección **Connected Apps** del dashboard.

---

## 2. TikTok (TikTok Content Posting API)
* **Tipo de Autenticación:** OAuth 2.0
* **Restricción oficial:** Las aplicaciones personales sin auditoría empresarial publican por defecto en modo privado (`SELF_ONLY`) o borrador en bandeja de entrada (`inbox`).
* **Pasos:**
  1. Regístrate en [TikTok for Developers](https://developers.tiktok.com/).
  2. Crea una app y selecciona el producto **Content Posting API**.
  3. Configura los scopes requeridos: `video.upload`, `video.publish`.
  4. Define la Redirect URI: `http://localhost:8000/api/connections/tiktok/callback`.
  5. Copia el `Client Key` y `Client Secret`.

---

## 3. Instagram (Meta Graph API)
* **Tipo de Autenticación:** OAuth 2.0 / User Token
* **Requisito obligatorio:** La cuenta de Instagram debe ser de tipo **Profesional** (Creador o Empresa) y estar vinculada a una Página de Facebook.
* **Pasos:**
  1. Ve a [Meta for Developers](https://developers.facebook.com/).
  2. Crea una App de tipo **Negocio (Business)**.
  3. Agrega el producto **Instagram Graph API**.
  4. En el Explorador de la Graph API, genera un User Token con los permisos:
     - `instagram_basic`
     - `instagram_content_publish`
     - `pages_show_list`
  5. Obtén tu `instagram_account_id` consultando `me/accounts?fields=instagram_business_account`.

---

## 4. Facebook Pages (Meta Graph API)
* **Permisos requeridos:** `pages_manage_posts`, `pages_read_engagement`.
* **Pasos:**
  1. En tu App de [Meta for Developers](https://developers.facebook.com/), agrega **Facebook Login for Business**.
  2. Obtén el Page Access Token permanente y el Page ID de tu página administrada.

---

## 5. Threads (Threads API)
* **Pasos:**
  1. En Meta for Developers, activa el caso de uso **Threads API**.
  2. Solicita los permisos: `threads_basic`, `threads_content_publish`.
  3. Genera el token de acceso de usuario para tu cuenta de Threads.

---

## 6. X / Twitter (Twitter API v2)
* **Límites:** El nivel gratuito (Free Tier) permite hasta 1,500 publicaciones mensuales.
* **Pasos:**
  1. Ingresa a [X Developer Portal](https://developer.x.com/).
  2. Crea un **Project** y una **App**.
  3. En **User authentication settings**, activa OAuth 2.0 con permisos `tweet.read`, `tweet.write`, `users.read`.
  4. Guarda el `API Key`, `API Secret` y `Bearer Token`.

---

## 7. LinkedIn (LinkedIn Community Management API)
* **Pasos:**
  1. Accede a [LinkedIn Developers](https://www.linkedin.com/developers/).
  2. Crea una App y asóciala a tu perfil o página de empresa.
  3. Solicita el producto **Share on LinkedIn** (permiso `w_member_social`).
  4. Genera el Access Token mediante el flujo OAuth 2.0.

---

## 8. Pinterest (Pinterest API v5)
* **Pasos:**
  1. Regístrate en [Pinterest Developers](https://developers.pinterest.com/).
  2. Crea una App con scopes `boards:read`, `pins:write`.
  3. Crea un tablero en tu cuenta y copia el `board_id` donde se publicarán los videos.

---

## 9. Bluesky (AT Protocol)
* **Tipo:** App Password (contraseña de aplicación segura).
* **Pasos:**
  1. Abre Bluesky en el navegador o móvil.
  2. Ve a **Configuración > Privacidad y Seguridad > Contraseñas de aplicación**.
  3. Haz clic en **Agregar contraseña de aplicación** con el nombre `AutoClip`.
  4. Copia la contraseña generada (formato `xxxx-xxxx-xxxx-xxxx`) e ingrésala junto con tu handle (ej. `tuusuario.bsky.social`).

---

## 10. Reddit (Reddit API OAuth 2.0)
* **Pasos:**
  1. Ingresa a [Reddit App Preferences](https://www.reddit.com/prefs/apps).
  2. Crea una app de tipo `script`.
  3. Copia el `client_id` (debajo del nombre de la app) y el `client_secret`.

---

## 11. Telegram (Bot API)
* **Pasos:**
  1. En Telegram, inicia un chat con [@BotFather](https://t.me/BotFather).
  2. Envía el comando `/newbot` y sigue las instrucciones para crear tu bot.
  3. Copia el token de API generado (ej. `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).
  4. Añade tu bot como administrador a tu canal de Telegram o inicia una conversación directa con él.
  5. Obtén tu `chat_id` reenviando un mensaje al bot [@userinfobot](https://t.me/userinfobot).

---

## 12. Discord (Webhooks)
* **Pasos:**
  1. En Discord, abre la configuración del canal donde deseas publicar clips.
  2. Ve a **Integraciones > Webhooks > Nuevo Webhook**.
  3. Asigna el nombre `AutoClip Publisher` y copia la URL del Webhook.

---

## 13. WordPress (REST API)
* **Pasos:**
  1. Ingresa al panel de administración de tu sitio WordPress (`/wp-admin`).
  2. Ve a **Usuarios > Perfil**.
  3. Baja hasta la sección **Contraseñas de aplicación**.
  4. Escribe `AutoClip` y haz clic en **Añadir nueva contraseña de aplicación**.
  5. Copia la clave de 24 caracteres generada.

---

## 14. Google Business Profile
* **Pasos:**
  1. Habilita **Google My Business API** en Google Cloud Console.
  2. Obtén las credenciales OAuth 2.0 con alcance `https://www.googleapis.com/auth/business.manage`.

---

## 15. Google Drive, OneDrive y Dropbox (Ingesta Cloud)
* **Google Drive:** Usa el mismo proyecto de Google Cloud habilitando **Google Drive API v3** con scope `drive.readonly`.
* **OneDrive:** Registra una app en [Azure Portal](https://portal.azure.com/) habilitando Microsoft Graph con permiso `Files.Read`.
* **Dropbox:** Crea una App en [Dropbox App Console](https://www.dropbox.com/developers/apps) con permiso `files.content.read` y genera un access token permanente.

---

## 16. Carpeta Local (Ingesta en Disco / LAN)
* No requiere claves ni conexión a Internet.
* Especifica cualquier directorio local accesible (ej. `C:\Videos\Cortes` o `D:\Podcast\Final`). El sistema detectará automáticamente los nuevos archivos `.mp4` y `.mov`.
