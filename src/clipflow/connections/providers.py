"""Definición y registro de proveedores sociales y de almacenamiento para Connected Apps Hub."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from clipflow.security import is_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)


@dataclass
class ProviderInfo:
    id: str
    name: str
    kind: str  # "social" | "cloud_storage" | "local_storage"
    auth_type: str  # "oauth2" | "bot_token" | "api_key" | "app_password" | "local_path"
    description: str
    known_limits: str
    required_fields: List[str]
    capabilities: List[str]
    doc_url: str


def _check_mock_credentials(credentials: Dict[str, Any]) -> Optional[Tuple[bool, str, Dict[str, Any]]]:
    """
    Bloquea cualquier intento de usar credenciales simuladas (mock) fuera de pruebas autorizadas.
    Retorna None si no es mock o si está permitido para que el proveedor prosiga.
    """
    if credentials.get("mock"):
        if not is_mock_allowed():
            return (
                False,
                "Modo mock prohibido en producción. Ningún proveedor puede conectarse con datos simulados.",
                {"mock_blocked": True},
            )
    return None


class BaseProvider(ABC):
    @property
    @abstractmethod
    def info(self) -> ProviderInfo:
        pass

    @abstractmethod
    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """
        Ejecuta una llamada de diagnóstico real a la API del proveedor.
        Retorna (exitoso: bool, mensaje: str, diagnostico: dict).
        """
        pass

    def refresh_token(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        """Renueva tokens OAuth expirados si el proveedor lo soporta."""
        return credentials

    def disconnect(self, credentials: Dict[str, Any]) -> bool:
        """Revoca tokens en el proveedor remoto si es posible."""
        return True


# -------------------------------------------------------------
# 1. YouTube (Google OAuth 2.0)
# -------------------------------------------------------------
class YouTubeProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="youtube",
            name="YouTube",
            kind="social",
            auth_type="oauth2",
            description="Publicación de YouTube Shorts y videos regulares mediante Google Data API v3 con OAuth 2.0 interactivo.",
            known_limits="Cuota inicial oficial: 100 llamadas diarias a videos.insert (1600 unidades por subida). Título máx. 100 caracteres. Desde nov 2026 proyectos no verificados pueden elegir privacidad (privado recomendado en pruebas).",
            required_fields=["client_id", "client_secret"],
            capabilities=["video_upload", "shorts_9_16", "custom_thumbnail", "read_comments", "reply_comments", "analytics"],
            doc_url="https://developers.google.com/youtube/v3/docs/videos/insert",
        )

    def refresh_token(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        refresh_tok = (credentials.get("refresh_token") or "").strip()
        client_id = (credentials.get("client_id") or "").strip()
        client_secret = (credentials.get("client_secret") or "").strip()
        if not (refresh_tok and client_id and client_secret):
            raise RuntimeError("Faltan client_id, client_secret o refresh_token para renovar la sesión de YouTube.")

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_tok,
                "grant_type": "refresh_token",
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Error al renovar token de YouTube (HTTP {resp.status_code}).")

        data = resp.json()
        updated = dict(credentials)
        if data.get("access_token"):
            updated["access_token"] = data["access_token"]
        if data.get("expires_in"):
            updated["expires_in"] = int(data["expires_in"])
        if data.get("scope"):
            updated["scopes"] = data["scope"].split()
        return updated

    def disconnect(self, credentials: Dict[str, Any]) -> bool:
        token = credentials.get("refresh_token") or credentials.get("access_token")
        if token and not credentials.get("mock"):
            try:
                requests.post(
                    "https://oauth2.googleapis.com/revoke",
                    params={"token": token},
                    headers={"content-type": "application/x-www-form-urlencoded"},
                    timeout=10.0,
                )
            except Exception:
                pass
        return True

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked

        if credentials.get("mock"):
            return True, "Canal conectado: @OwnerYouTube (Modo Test)", {
                "channel_id": "UC_MOCK_123",
                "channel_title": "OwnerYouTube",
                "subscribers": 1500,
            }

        access_token = (credentials.get("access_token") or "").strip()
        refresh_tok = (credentials.get("refresh_token") or "").strip()
        refreshed_creds: Optional[Dict[str, Any]] = None

        if not access_token and refresh_tok and credentials.get("client_id") and credentials.get("client_secret"):
            try:
                refreshed_creds = self.refresh_token(credentials)
                access_token = refreshed_creds.get("access_token", "")
            except Exception as e:
                return False, redact_secrets(f"No se pudo renovar el token OAuth de YouTube: {e}"), {"status": 401}

        if not access_token:
            if credentials.get("client_id") and credentials.get("client_secret"):
                return (
                    False,
                    "Client ID y Client Secret guardados. Falta completar la autorización interactiva con Google OAuth 2.0.",
                    {"needs_oauth": True},
                )
            return False, "Faltan credenciales OAuth de YouTube (client_id, client_secret y autorización).", {}

        try:
            resp = requests.get(
                "https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&mine=true",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0,
            )
            if resp.status_code == 401 and refresh_tok and credentials.get("client_id") and credentials.get("client_secret"):
                try:
                    refreshed_creds = self.refresh_token(credentials)
                    access_token = refreshed_creds.get("access_token", "")
                    resp = requests.get(
                        "https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics&mine=true",
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10.0,
                    )
                except Exception as e:
                    return False, redact_secrets(f"Token expirado y fallo en renovación automática: {e}"), {"status": 401}

            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    ch = items[0]
                    title = ch.get("snippet", {}).get("title", "Canal")
                    ch_id = ch.get("id", "")
                    sub_count = ch.get("statistics", {}).get("subscriberCount", "0")
                    diag: Dict[str, Any] = {
                        "channel_id": ch_id,
                        "channel_title": title,
                        "subscribers": sub_count,
                        "raw": ch,
                    }
                    if refreshed_creds:
                        diag["_refreshed_credentials"] = refreshed_creds
                    return True, f"Canal verificado: '{title}' (ID: {ch_id}, {sub_count} suscriptores).", diag
                return False, "Cuenta Google autenticada, pero no se encontró un canal de YouTube activo.", {}
            elif resp.status_code == 401:
                return False, "El token de acceso ha expirado o es inválido. Se requiere re-autorización OAuth.", {"status": 401}
            elif resp.status_code == 403:
                return False, "Permisos insuficientes o YouTube Data API v3 no habilitada en Google Cloud.", {"status": 403}
            return False, redact_secrets(f"Error HTTP {resp.status_code} desde YouTube API."), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Excepción de conexión con YouTube: {e}"), {}


# -------------------------------------------------------------
# 2. TikTok (TikTok Content Posting API)
# -------------------------------------------------------------
class TikTokProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="tiktok",
            name="TikTok",
            kind="social",
            auth_type="oauth2",
            description="Publicación de videos verticales mediante TikTok Content Posting API v2 con OAuth 2.0 interactivo.",
            known_limits="Apps sin auditoría comercial quedan restringidas a visualización privada (SELF_ONLY) o borrador en bandeja de entrada (fallback_to_inbox -> ACTION_REQUIRED).",
            required_fields=["client_key", "client_secret"],
            capabilities=["video_upload", "direct_post", "inbox_draft"],
            doc_url="https://developers.tiktok.com/doc/content-posting-api-reference-direct-post",
        )

    def refresh_token(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        refresh_tok = (credentials.get("refresh_token") or "").strip()
        client_key = (credentials.get("client_key") or "").strip()
        client_secret = (credentials.get("client_secret") or "").strip()
        if not (refresh_tok and client_key and client_secret):
            raise RuntimeError("Faltan client_key, client_secret o refresh_token para renovar la sesión de TikTok.")

        resp = requests.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": client_key,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_tok,
            },
            timeout=15.0,
        )
        data = resp.json() if resp.text.startswith("{") else {}
        if resp.status_code != 200 or data.get("error"):
            err = data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}"
            raise RuntimeError(redact_secrets(f"Error al renovar token de TikTok: {err}"))

        updated = dict(credentials)
        if data.get("access_token"):
            updated["access_token"] = data["access_token"]
        if data.get("refresh_token"):
            updated["refresh_token"] = data["refresh_token"]
        if data.get("expires_in"):
            updated["expires_in"] = int(data["expires_in"])
        if data.get("scope"):
            updated["scopes"] = [s.strip() for s in data["scope"].replace(",", " ").split() if s.strip()]
        return updated

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked

        if credentials.get("mock"):
            return True, "Cuenta TikTok conectada: @tiktok_owner (Audit Pending: SELF_ONLY)", {
                "open_id": "mock_open_id",
                "channel_id": "mock_open_id",
                "channel_title": "tiktok_owner",
            }

        token = (credentials.get("access_token") or "").strip()
        refresh_tok = (credentials.get("refresh_token") or "").strip()
        refreshed_creds: Optional[Dict[str, Any]] = None

        if not token and refresh_tok and credentials.get("client_key") and credentials.get("client_secret"):
            try:
                refreshed_creds = self.refresh_token(credentials)
                token = refreshed_creds.get("access_token", "")
            except Exception as e:
                return False, redact_secrets(f"No se pudo renovar token OAuth de TikTok: {e}"), {"status": 401}

        if not token:
            if credentials.get("client_key") and credentials.get("client_secret"):
                return (
                    False,
                    "Client Key y Client Secret guardados. Falta completar la autorización interactiva con TikTok OAuth 2.0.",
                    {"needs_oauth": True},
                )
            return False, "Faltan credenciales OAuth de TikTok (client_key, client_secret y autorización).", {}

        try:
            resp = requests.get(
                "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 401 and refresh_tok and credentials.get("client_key") and credentials.get("client_secret"):
                try:
                    refreshed_creds = self.refresh_token(credentials)
                    token = refreshed_creds.get("access_token", "")
                    resp = requests.get(
                        "https://open.tiktokapis.com/v2/user/info/?fields=open_id,union_id,avatar_url,display_name",
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=10.0,
                    )
                except Exception as e:
                    return False, redact_secrets(f"Token de TikTok expirado y fallo al renovar: {e}"), {"status": 401}

            if resp.status_code == 200:
                user = resp.json().get("data", {}).get("user", {})
                name = user.get("display_name") or "TikTok User"
                open_id = user.get("open_id") or ""
                diag: Dict[str, Any] = {
                    "open_id": open_id,
                    "channel_id": open_id,
                    "channel_title": name,
                    "raw": user,
                }
                if refreshed_creds:
                    diag["_refreshed_credentials"] = refreshed_creds
                return True, f"Cuenta verificada: {name} (Restricción por defecto: SELF_ONLY)", diag
            if resp.status_code == 401:
                return False, "El token de TikTok ha expirado. Se requiere re-autorización OAuth.", {"status": 401}
            if resp.status_code == 403:
                return False, "Permisos insuficientes en TikTok (revisa scopes user.info.basic y video.upload).", {"status": 403}
            return False, redact_secrets(f"TikTok HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error al verificar TikTok: {e}"), {}


# -------------------------------------------------------------
# 3. Instagram (Meta Graph API)
# -------------------------------------------------------------
class InstagramProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="instagram",
            name="Instagram",
            kind="social",
            auth_type="oauth2",
            description="Publicación de Reels y carruseles mediante Instagram Graph API (Requiere cuenta Profesional / Creator).",
            known_limits="Máximo 50 posts cada 24 horas por cuenta. Ratio 9:16 recomendado para Reels. Audio AAC obligatorio.",
            required_fields=["access_token", "instagram_account_id"],
            capabilities=["reels_upload", "stories", "read_comments", "reply_comments", "analytics"],
            doc_url="https://developers.facebook.com/docs/instagram-api/guides/content-publishing-api",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        ig_id = credentials.get("instagram_account_id")
        if not token or not ig_id:
            return False, "Faltan access_token o instagram_account_id.", {}
        if credentials.get("mock"):
            return True, f"Instagram Pro conectado: ID {ig_id}", {"username": "instagram_owner"}
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{ig_id}",
                params={"fields": "username,name,profile_picture_url"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Cuenta verificada: @{data.get('username')}", data
            return False, redact_secrets(f"Instagram HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error al consultar Instagram: {e}"), {}


# -------------------------------------------------------------
# 4. Facebook Pages (Meta Graph API)
# -------------------------------------------------------------
class FacebookPagesProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="facebook",
            name="Facebook Pages",
            kind="social",
            auth_type="oauth2",
            description="Publicación de videos y Reels en Páginas de Facebook mediante Meta Graph API.",
            known_limits="Permiso pages_manage_posts requerido. Máx. 4GB de video.",
            required_fields=["page_access_token", "page_id"],
            capabilities=["video_upload", "reels_upload", "page_posts", "read_comments"],
            doc_url="https://developers.facebook.com/docs/video-api",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("page_access_token")
        page_id = credentials.get("page_id")
        if not token or not page_id:
            return False, "Faltan page_access_token o page_id.", {}
        if credentials.get("mock"):
            return True, f"Página de Facebook conectada: ID {page_id}", {"name": "Mi Página"}
        try:
            resp = requests.get(
                f"https://graph.facebook.com/v19.0/{page_id}",
                params={"fields": "name,link,fan_count"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Página confirmada: '{data.get('name')}' ({data.get('fan_count', 0)} fans)", data
            return False, redact_secrets(f"Facebook HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Facebook: {e}"), {}


# -------------------------------------------------------------
# 5. Threads (Threads API)
# -------------------------------------------------------------
class ThreadsProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="threads",
            name="Threads",
            kind="social",
            auth_type="oauth2",
            description="Publicación de videos, imágenes y texto mediante la API oficial de Threads.",
            known_limits="Máximo 250 publicaciones cada 24 horas por usuario. Duración máx de video: 5 minutos.",
            required_fields=["access_token", "user_id"],
            capabilities=["video_post", "text_post", "replies"],
            doc_url="https://developers.facebook.com/docs/threads",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de Threads.", {}
        if credentials.get("mock"):
            return True, "Cuenta Threads conectada.", {"username": "threads_owner"}
        try:
            resp = requests.get(
                "https://graph.threads.net/v1.0/me",
                params={"fields": "id,username"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Threads verificado: @{data.get('username')}", data
            return False, redact_secrets(f"Threads HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Threads: {e}"), {}


# -------------------------------------------------------------
# 6. X (Twitter API v2)
# -------------------------------------------------------------
class XProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="x",
            name="X (Twitter)",
            kind="social",
            auth_type="oauth2",
            description="Publicación de clips y posts en X mediante API v2.",
            known_limits="Plan Free permite 1500 tweets/mes. Plan Basic (pago) requerido para uploads multimedia intensivos. Límite texto: 280 caracteres.",
            required_fields=["bearer_token", "api_key", "api_secret"],
            capabilities=["video_upload", "tweet_post", "analytics"],
            doc_url="https://developer.x.com/en/docs/x-api",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("bearer_token")
        if not token:
            return False, "Falta bearer_token de X.", {}
        if credentials.get("mock"):
            return True, "X conectado: @x_owner", {"username": "x_owner"}
        try:
            resp = requests.get(
                "https://api.twitter.com/2/users/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                return True, f"X verificado: @{data.get('username')}", data
            return False, redact_secrets(f"X API HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en X API: {e}"), {}


# -------------------------------------------------------------
# 7. LinkedIn (LinkedIn Community API)
# -------------------------------------------------------------
class LinkedInProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="linkedin",
            name="LinkedIn",
            kind="social",
            auth_type="oauth2",
            description="Publicación de videos y artículos en perfiles o páginas corporativas.",
            known_limits="Subida de video requiere proceso de 3 pasos (registerUpload, PUT binario, createPost).",
            required_fields=["access_token", "person_urn"],
            capabilities=["video_share", "text_post", "analytics"],
            doc_url="https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de LinkedIn.", {}
        if credentials.get("mock"):
            return True, "LinkedIn conectado: Propietario", {"urn": "urn:li:person:mock123"}
        try:
            resp = requests.get(
                "https://api.linkedin.com/v2/userinfo",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"LinkedIn verificado: {data.get('name')}", data
            return False, redact_secrets(f"LinkedIn HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en LinkedIn: {e}"), {}


# -------------------------------------------------------------
# 8. Pinterest (Pinterest API v5)
# -------------------------------------------------------------
class PinterestProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="pinterest",
            name="Pinterest",
            kind="social",
            auth_type="oauth2",
            description="Publicación de Video Pins e Idea Pins.",
            known_limits="Requiere tablero de destino (board_id). Video 9:16 de hasta 15 minutos.",
            required_fields=["access_token", "board_id"],
            capabilities=["video_pin", "board_publish"],
            doc_url="https://developers.pinterest.com/docs/api/v5/",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de Pinterest.", {}
        if credentials.get("mock"):
            return True, "Pinterest conectado: @pinterest_owner", {}
        try:
            resp = requests.get(
                "https://api.pinterest.com/v5/user_account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                return True, f"Pinterest verificado: @{resp.json().get('username')}", resp.json()
            return False, redact_secrets(f"Pinterest HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Pinterest: {e}"), {}


# -------------------------------------------------------------
# 9. Bluesky (AT Protocol)
# -------------------------------------------------------------
class BlueskyProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="bluesky",
            name="Bluesky",
            kind="social",
            auth_type="app_password",
            description="Publicación descentralizada en la red abierta AT Protocol mediante App Password.",
            known_limits="Límite texto 300 caracteres. Subida de videos mediante /xrpc/app.bsky.video.uploadVideo.",
            required_fields=["handle", "app_password"],
            capabilities=["video_post", "text_post", "at_protocol"],
            doc_url="https://docs.bsky.app/",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        handle = credentials.get("handle")
        password = credentials.get("app_password")
        if not handle or not password:
            return False, "Faltan handle o app_password.", {}
        if credentials.get("mock"):
            return True, f"Bluesky conectado: @{handle}", {"did": "did:plc:mock"}
        try:
            resp = requests.post(
                "https://bsky.social/xrpc/com.atproto.server.createSession",
                json={"identifier": handle, "password": password},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Bluesky verificado: @{data.get('handle')}", {"did": data.get("did")}
            return False, redact_secrets(f"Bluesky HTTP {resp.status_code}"), {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Bluesky: {e}"), {}


# -------------------------------------------------------------
# 10. Reddit (Reddit API OAuth 2.0)
# -------------------------------------------------------------
class RedditProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="reddit",
            name="Reddit",
            kind="social",
            auth_type="oauth2",
            description="Publicación de videos en subreddits propios o autorizados.",
            known_limits="Los subreddits pueden imponer reglas de karma o formato de video.",
            required_fields=["client_id", "client_secret", "username", "password"],
            capabilities=["video_submit", "text_post"],
            doc_url="https://www.reddit.com/dev/api/",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        if credentials.get("mock"):
            return True, "Reddit conectado: u/reddit_owner", {}
        client_id = credentials.get("client_id")
        secret = credentials.get("client_secret")
        username = credentials.get("username")
        password = credentials.get("password")
        if not all([client_id, secret, username, password]):
            return False, "Faltan client_id, client_secret, username o password de Reddit.", {}
        try:
            resp = requests.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=(client_id, secret),
                data={"grant_type": "password", "username": username, "password": password},
                headers={"User-Agent": "AutoClipPublisher/1.0"},
                timeout=10.0,
            )
            if resp.status_code == 200 and resp.json().get("access_token"):
                return True, f"Reddit verificado: u/{username}", {"username": username}
            return False, f"Reddit rechazó la autenticación (HTTP {resp.status_code}).", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error al verificar Reddit: {e}"), {}


# -------------------------------------------------------------
# 11. Telegram (Bot API)
# -------------------------------------------------------------
class TelegramProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="telegram",
            name="Telegram",
            kind="social",
            auth_type="bot_token",
            description="Envío de clips a canales, grupos o chats privados de Telegram mediante Bot API.",
            known_limits="Tamaño máximo de archivo: 50MB mediante Bot API estándar. Para archivos de hasta 2GB se requiere Local Bot API Server.",
            required_fields=["bot_token", "chat_id"],
            capabilities=["video_send", "message_send", "channel_broadcast"],
            doc_url="https://core.telegram.org/bots/api#sendvideo",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("bot_token")
        chat_id = credentials.get("chat_id")
        if not token or not chat_id:
            return False, "Faltan bot_token o chat_id de Telegram.", {}
        if credentials.get("mock"):
            return True, f"Telegram Bot conectado a chat {chat_id}", {"username": "clipflow_bot"}
        try:
            resp = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10.0)
            if resp.status_code == 200 and resp.json().get("ok"):
                data = resp.json().get("result", {})
                return True, f"Bot activo: @{data.get('username')}", data
            return False, f"Telegram HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Telegram: {e}"), {}


# -------------------------------------------------------------
# 12. Discord (Webhooks / Bot API)
# -------------------------------------------------------------
class DiscordProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="discord",
            name="Discord",
            kind="social",
            auth_type="api_key",
            description="Notificación y publicación de videos en canales de Discord mediante Webhooks.",
            known_limits="Límite de subida: 25MB para cuentas regulares sin Nitro en el servidor de destino.",
            required_fields=["webhook_url"],
            capabilities=["webhook_video", "embed_message"],
            doc_url="https://discord.com/developers/docs/resources/webhook",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        url = credentials.get("webhook_url")
        if not url:
            return False, "Falta webhook_url de Discord.", {}
        if credentials.get("mock"):
            return True, "Discord Webhook verificado.", {}
        try:
            resp = requests.get(url, timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Webhook de Discord verificado para canal ID: {data.get('channel_id')}", data
            return False, f"Discord Webhook HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Discord Webhook: {e}"), {}


# -------------------------------------------------------------
# 13. WordPress (REST API)
# -------------------------------------------------------------
class WordPressProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="wordpress",
            name="WordPress",
            kind="social",
            auth_type="app_password",
            description="Publicación automática de artículos y videos incrustados en sitios WordPress.",
            known_limits="Requiere soporte de REST API y Application Passwords activadas.",
            required_fields=["site_url", "username", "app_password"],
            capabilities=["post_publish", "media_upload"],
            doc_url="https://developer.wordpress.org/rest-api/",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        site = credentials.get("site_url")
        user = credentials.get("username")
        pwd = credentials.get("app_password")
        if not site or not user or not pwd:
            return False, "Faltan site_url, username o app_password.", {}
        if credentials.get("mock"):
            return True, f"WordPress verificado en {site}", {}
        try:
            clean_url = site.rstrip("/")
            resp = requests.get(f"{clean_url}/wp-json/wp/v2/users/me", auth=(user, pwd), timeout=10.0)
            if resp.status_code == 200:
                data = resp.json()
                return True, f"WordPress verificado para usuario '{data.get('name')}'", data
            return False, f"WordPress HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en WordPress: {e}"), {}


# -------------------------------------------------------------
# 14. Google Business Profile
# -------------------------------------------------------------
class GoogleBusinessProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="google_business",
            name="Google Business Profile",
            kind="social",
            auth_type="oauth2",
            description="Publicación de actualizaciones y videos en el perfil de empresa de Google.",
            known_limits="Requiere verificación del perfil de negocio y aprobación de API en Google Cloud.",
            required_fields=["access_token", "account_id", "location_id"],
            capabilities=["local_post", "media_upload"],
            doc_url="https://developers.google.com/my-business",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        if credentials.get("mock"):
            return True, "Google Business Profile verificado.", {}
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de Google Business Profile.", {}
        try:
            resp = requests.get(
                "https://mybusinessaccountmanagement.googleapis.com/v1/accounts",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                accounts = resp.json().get("accounts", [])
                return True, f"Google Business verificado ({len(accounts)} cuentas).", {"accounts_count": len(accounts)}
            return False, f"Google Business HTTP {resp.status_code} (verifique aprobación de API).", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Google Business Profile: {e}"), {}


# -------------------------------------------------------------
# 15. Google Drive (Cloud Storage Input)
# -------------------------------------------------------------
class GoogleDriveProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="google_drive",
            name="Google Drive",
            kind="cloud_storage",
            auth_type="oauth2",
            description="Ingesta automática de videos y directos almacenados en Google Drive.",
            known_limits="Sujeto a cuotas de Google Drive API v3.",
            required_fields=["access_token", "folder_id"],
            capabilities=["file_download", "folder_watch"],
            doc_url="https://developers.google.com/drive/api/v3/about-sdk",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de Google Drive.", {}
        if credentials.get("mock"):
            return True, "Google Drive conectado y carpeta accesible.", {}
        try:
            resp = requests.get(
                "https://www.googleapis.com/drive/v3/about?fields=user",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                user = resp.json().get("user", {})
                return True, f"Google Drive verificado: {user.get('displayName')} ({user.get('emailAddress')})", user
            return False, f"Google Drive HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Google Drive: {e}"), {}


# -------------------------------------------------------------
# 16. OneDrive (Microsoft Graph API)
# -------------------------------------------------------------
class OneDriveProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="onedrive",
            name="OneDrive",
            kind="cloud_storage",
            auth_type="oauth2",
            description="Ingesta de videos desde carpetas de Microsoft OneDrive personal o empresarial.",
            known_limits="Sujeto a Microsoft Graph API throttling.",
            required_fields=["access_token"],
            capabilities=["file_download", "folder_watch"],
            doc_url="https://learn.microsoft.com/en-us/onedrive/developer/rest-api/",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de OneDrive.", {}
        if credentials.get("mock"):
            return True, "OneDrive conectado.", {}
        try:
            resp = requests.get(
                "https://graph.microsoft.com/v1.0/me/drive",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"OneDrive verificado: Tipo {data.get('driveType')}", data
            return False, f"OneDrive HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en OneDrive: {e}"), {}


# -------------------------------------------------------------
# 17. Dropbox (Dropbox API)
# -------------------------------------------------------------
class DropboxProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="dropbox",
            name="Dropbox",
            kind="cloud_storage",
            auth_type="api_key",
            description="Ingesta de grabaciones y podcasts desde Dropbox.",
            known_limits="Descarga de archivos sujeta a ancho de banda de Dropbox.",
            required_fields=["access_token"],
            capabilities=["file_download", "folder_watch"],
            doc_url="https://www.dropbox.com/developers/documentation",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        blocked = _check_mock_credentials(credentials)
        if blocked:
            return blocked
        token = credentials.get("access_token")
        if not token:
            return False, "Falta access_token de Dropbox.", {}
        if credentials.get("mock"):
            return True, "Dropbox conectado.", {}
        try:
            resp = requests.post(
                "https://api.dropboxapi.com/2/users/get_current_account",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                return True, f"Dropbox verificado: {data.get('name', {}).get('display_name')}", data
            return False, f"Dropbox HTTP {resp.status_code}", {"status": resp.status_code}
        except Exception as e:
            return False, redact_secrets(f"Error en Dropbox: {e}"), {}


# -------------------------------------------------------------
# 18. Local Directory (Watched Storage)
# -------------------------------------------------------------
class LocalDirectoryProvider(BaseProvider):
    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            id="local_directory",
            name="Carpeta Local",
            kind="local_storage",
            auth_type="local_path",
            description="Monitoreo de carpetas en disco local o red LAN para importar videos automáticamente.",
            known_limits="Acceso directo a disco sin costos de API externa.",
            required_fields=["local_directory_path"],
            capabilities=["file_scan", "instant_import"],
            doc_url="https://docs.python.org/3/library/pathlib.html",
        )

    def test_connection(self, credentials: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        path_str = credentials.get("local_directory_path")
        if not path_str:
            return False, "Falta especificar la ruta de la carpeta local.", {}
        p = Path(path_str)
        if not p.exists():
            return False, f"La carpeta '{path_str}' no existe.", {}
        if not p.is_dir():
            return False, f"La ruta '{path_str}' no es un directorio.", {}

        video_count = sum(1 for ext in ("*.mp4", "*.mov", "*.mkv") for _ in p.glob(ext))
        return True, f"Directorio accesible con {video_count} videos detectados.", {"path": str(p), "video_count": video_count}


# -------------------------------------------------------------
# Registry Global de Proveedores
# -------------------------------------------------------------
ALL_PROVIDERS: List[BaseProvider] = [
    YouTubeProvider(),
    TikTokProvider(),
    InstagramProvider(),
    FacebookPagesProvider(),
    ThreadsProvider(),
    XProvider(),
    LinkedInProvider(),
    PinterestProvider(),
    BlueskyProvider(),
    RedditProvider(),
    TelegramProvider(),
    DiscordProvider(),
    WordPressProvider(),
    GoogleBusinessProvider(),
    GoogleDriveProvider(),
    OneDriveProvider(),
    DropboxProvider(),
    LocalDirectoryProvider(),
]

PROVIDERS_MAP: Dict[str, BaseProvider] = {p.info.id: p for p in ALL_PROVIDERS}


def get_provider(provider_id: str) -> Optional[BaseProvider]:
    return PROVIDERS_MAP.get(provider_id.lower())
