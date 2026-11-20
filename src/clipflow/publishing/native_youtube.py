"""Conector nativo directo para YouTube Data API v3 y Google OAuth 2.0."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

from clipflow.publishing.base import BasePublisher, PublishResult

logger = logging.getLogger(__name__)

YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class NativeYouTubePublisher(BasePublisher):
    """
    Publicador nativo de YouTube mediante Google OAuth 2.0 y YouTube Data API v3.
    No requiere intermediarios ni servicios SaaS de terceros.
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: str = "http://localhost:8000/api/connections/youtube/callback",
        mock_mode: bool = False,
    ):
        self.client_id = (client_id or os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
        self.client_secret = (client_secret or os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()
        self.access_token = (access_token or os.getenv("YOUTUBE_ACCESS_TOKEN") or "").strip()
        self.refresh_token = (refresh_token or os.getenv("YOUTUBE_REFRESH_TOKEN") or "").strip()
        self.redirect_uri = redirect_uri
        self.mock_mode = mock_mode
        self._mock_videos: Dict[str, Dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        if self.mock_mode:
            return True
        return bool(self.client_id and self.client_secret and (self.access_token or self.refresh_token))

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Genera el enlace de consentimiento OAuth 2.0 para el propietario."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{YOUTUBE_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Intercambia el código de autorización por tokens de acceso y renovación."""
        if self.mock_mode:
            return {
                "access_token": "mock-yt-access-token",
                "refresh_token": "mock-yt-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
            }

        payload = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "grant_type": "authorization_code",
        }
        resp = requests.post(YOUTUBE_TOKEN_URL, data=payload, timeout=15.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al canjear código OAuth: {resp.text}")

        data = resp.json()
        self.access_token = data.get("access_token", "")
        if "refresh_token" in data:
            self.refresh_token = data["refresh_token"]
        return data

    def refresh_access_token(self) -> str:
        """Renueva el token de acceso expirado utilizando el refresh_token."""
        if self.mock_mode:
            self.access_token = f"refreshed-mock-token-{int(time.time())}"
            return self.access_token

        if not self.refresh_token:
            raise RuntimeError("No hay refresh_token disponible para renovar la sesión de YouTube.")

        payload = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        }
        resp = requests.post(YOUTUBE_TOKEN_URL, data=payload, timeout=15.0)
        if resp.status_code != 200:
            raise RuntimeError(f"Error al renovar token de YouTube: {resp.text}")

        data = resp.json()
        self.access_token = data.get("access_token", "")
        return self.access_token

    def _get_authenticated_headers(self) -> Dict[str, str]:
        if not self.access_token and self.refresh_token:
            self.refresh_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def list_profiles(self) -> List[Dict[str, Any]]:
        """Recupera los datos del canal de YouTube autenticado."""
        if self.mock_mode:
            return [
                {
                    "id": "UC_MOCK_CHANNEL_ID",
                    "username": "OwnerYouTubeChannel",
                    "connected_platforms": ["youtube"],
                    "reconnect_platforms": [],
                    "title": "Owner Personal Studio",
                }
            ]

        if not self.is_configured:
            return []

        try:
            headers = self._get_authenticated_headers()
            resp = requests.get(
                f"{YOUTUBE_API_BASE}/channels?part=snippet,statistics&mine=true",
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code == 401:
                # Token expirado, renovar y reintentar
                self.refresh_access_token()
                headers = self._get_authenticated_headers()
                resp = requests.get(
                    f"{YOUTUBE_API_BASE}/channels?part=snippet,statistics&mine=true",
                    headers=headers,
                    timeout=10.0,
                )

            if resp.status_code != 200:
                logger.warning("Error al consultar canal de YouTube: %d %s", resp.status_code, resp.text)
                return []

            items = resp.json().get("items", [])
            profiles = []
            for item in items:
                snippet = item.get("snippet", {})
                profiles.append({
                    "id": item.get("id"),
                    "username": snippet.get("title", "YouTube Channel"),
                    "connected_platforms": ["youtube"],
                    "reconnect_platforms": [],
                    "custom_url": snippet.get("customUrl"),
                    "raw": item,
                })
            return profiles
        except Exception as e:
            logger.error("Error al obtener perfil de YouTube: %s", e)
            return []

    def verify_account(self, profile: str, platform: str) -> bool:
        if platform.lower() != "youtube":
            return False
        if self.mock_mode:
            return True
        profiles = self.list_profiles()
        return len(profiles) > 0

    def submit_post(
        self,
        video_path: str,
        title: str,
        platform: str,
        profile: str,
        description: Optional[str] = None,
        visibility: str = "private",
        scheduled_date_iso: Optional[str] = None,
        timezone_str: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        output_variant_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> PublishResult:
        """
        Sube el video a YouTube mediante la API oficial.
        Soporta modos private, unlisted y public.
        """
        req_id = f"yt-native-{uuid.uuid4()}"

        if self.mock_mode:
            video_id = f"yt_mock_{int(time.time())}"
            video_info = {
                "id": video_id,
                "title": title[:100],
                "description": description or "",
                "status": "PUBLISHED" if not scheduled_date_iso else "SCHEDULED",
                "privacy": visibility,
                "url": f"https://www.youtube.com/shorts/{video_id}",
            }
            self._mock_videos[video_id] = video_info
            self._mock_videos[req_id] = video_info
            return PublishResult(
                ok=True,
                request_id=req_id,
                job_id=video_id,
                status="PUBLISHED" if not scheduled_date_iso else "QUEUED",
                remote_post_id=video_id,
                remote_url=f"https://www.youtube.com/shorts/{video_id}",
                raw_payload={"mock": True, "videoId": video_id},
            )

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="YouTube nativo no está configurado (faltan credenciales OAuth).",
            )

        path = Path(video_path)
        if not path.is_file():
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=f"Archivo no encontrado: {video_path}",
            )

        # 1. Preparar metadatos (límite estricto de título de 100 caracteres en YouTube)
        clean_title = title.strip()[:100]
        desc = description or ""
        if "#Shorts" not in desc and "#Shorts" not in clean_title:
            desc = f"{desc}\n\n#Shorts".strip()

        snippet = {
            "title": clean_title,
            "description": desc,
            "categoryId": "22",
            "defaultLanguage": "es",
        }
        status_dict: Dict[str, Any] = {
            "privacyStatus": visibility.lower() if visibility in ("private", "unlisted", "public") else "private",
            "selfDeclaredMadeForKids": False,
        }
        if scheduled_date_iso and visibility.lower() == "private":
            status_dict["publishAt"] = scheduled_date_iso

        metadata = {"snippet": snippet, "status": status_dict}

        # 2. Iniciar subida resumible
        try:
            headers = self._get_authenticated_headers()
            headers["X-Upload-Content-Type"] = "video/mp4"
            headers["X-Upload-Content-Length"] = str(path.stat().st_size)
            headers["Content-Type"] = "application/json; charset=UTF-8"

            init_resp = requests.post(
                f"{YOUTUBE_UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                headers=headers,
                data=json.dumps(metadata),
                timeout=30.0,
            )

            if init_resp.status_code not in (200, 201):
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error=f"YouTube rechazó inicio de subida: {init_resp.status_code} {init_resp.text}",
                )

            upload_url = init_resp.headers.get("Location")
            if not upload_url:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error="YouTube no devolvió Location para resumable upload.",
                )

            # 3. Transmitir el archivo de video
            with open(path, "rb") as video_file:
                upload_headers = {
                    "Content-Type": "video/mp4",
                    "Content-Length": str(path.stat().st_size),
                }
                upload_resp = requests.put(
                    upload_url,
                    headers=upload_headers,
                    data=video_file,
                    timeout=300.0,
                )

            if upload_resp.status_code in (200, 201):
                res_data = upload_resp.json()
                video_id = res_data.get("id")
                video_url = f"https://www.youtube.com/shorts/{video_id}"
                return PublishResult(
                    ok=True,
                    request_id=req_id,
                    job_id=video_id,
                    status="PUBLISHED" if not scheduled_date_iso else "QUEUED",
                    remote_post_id=video_id,
                    remote_url=video_url,
                    raw_payload=res_data,
                )
            else:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error=f"Error durante transmisión de video a YouTube: {upload_resp.status_code} {upload_resp.text}",
                )

        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=f"Excepción en subida nativa a YouTube: {e}",
            )

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """Consulta el estado del video en YouTube."""
        if self.mock_mode:
            video = self._mock_videos.get(request_id)
            if video:
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=request_id,
                    status=video["status"],
                    remote_post_id=request_id,
                    remote_url=video["url"],
                    raw_payload=video,
                )
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Video no encontrado")

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No configurado")

        try:
            headers = self._get_authenticated_headers()
            resp = requests.get(
                f"{YOUTUBE_API_BASE}/videos?part=snippet,status,statistics&id={request_id}",
                headers=headers,
                timeout=10.0,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    v = items[0]
                    return PublishResult(
                        ok=True,
                        request_id=request_id,
                        job_id=request_id,
                        status="PUBLISHED",
                        remote_post_id=v.get("id"),
                        remote_url=f"https://www.youtube.com/shorts/{v.get('id')}",
                        raw_payload=v,
                    )
                return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Video no encontrado en YouTube")
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=resp.text)
        except Exception as e:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=str(e))

    def cancel_scheduled(self, job_id: str) -> bool:
        """Elimina un video o borrador si aún no se ha publicado."""
        if self.mock_mode:
            if job_id in self._mock_videos:
                del self._mock_videos[job_id]
                return True
            return False

        if not self.is_configured:
            return False

        try:
            headers = self._get_authenticated_headers()
            resp = requests.delete(
                f"{YOUTUBE_API_BASE}/videos?id={job_id}",
                headers=headers,
                timeout=15.0,
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error("Error al cancelar video en YouTube: %s", e)
            return False

    def fetch_comments(self, video_id: str, max_results: int = 20) -> List[Dict[str, Any]]:
        """Obtiene comentarios reales del video mediante YouTube Data API v3."""
        if self.mock_mode:
            return [
                {
                    "comment_id": "mock-comment-1",
                    "author": "TechFan2026",
                    "text": "Excelente clip! Muy bien explicado.",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                }
            ]

        if not self.is_configured:
            return []

        try:
            headers = self._get_authenticated_headers()
            url = f"{YOUTUBE_API_BASE}/commentThreads?part=snippet&videoId={video_id}&maxResults={max_results}"
            resp = requests.get(url, headers=headers, timeout=10.0)
            if resp.status_code != 200:
                logger.warning("No se pudieron leer comentarios para video %s: %d", video_id, resp.status_code)
                return []

            comments = []
            for item in resp.json().get("items", []):
                top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
                comments.append({
                    "comment_id": item.get("id"),
                    "author": top.get("authorDisplayName", "Anónimo"),
                    "author_id": top.get("authorChannelId", {}).get("value"),
                    "text": top.get("textDisplay", ""),
                    "published_at": top.get("publishedAt"),
                })
            return comments
        except Exception as e:
            logger.error("Error al leer comentarios de YouTube: %s", e)
            return []

    def reply_to_comment(self, comment_id: str, text: str) -> bool:
        """Responde a un comentario de forma oficial mediante YouTube Data API v3."""
        if self.mock_mode:
            return True

        if not self.is_configured:
            return False

        try:
            headers = self._get_authenticated_headers()
            headers["Content-Type"] = "application/json; charset=UTF-8"
            body = {
                "snippet": {
                    "parentId": comment_id,
                    "textOriginal": text,
                }
            }
            resp = requests.post(
                f"{YOUTUBE_API_BASE}/comments?part=snippet",
                headers=headers,
                data=json.dumps(body),
                timeout=10.0,
            )
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.error("Error al responder comentario en YouTube: %s", e)
            return False
