"""Conector nativo directo para YouTube Data API v3 con Google OAuth 2.0 y subida resumible por bloques."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import requests

from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.security import assert_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)

YOUTUBE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
YOUTUBE_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"

# Tamaño de bloque múltiplo de 256 KB requerido por el protocolo resumible de Google (4 MB por defecto)
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024

DEFAULT_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]

_RANGE_RE = re.compile(r"bytes=0-(\d+)")


class NativeYouTubePublisher(BasePublisher):
    """
    Publicador nativo de YouTube mediante Google OAuth 2.0 y YouTube Data API v3.
    Soporta subida resumible por bloques con recuperación parcial, reconciliación
    por video_id real y cancelación local segura (sin eliminar videos ya subidos).
    """

    def __init__(
        self,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: str = "http://127.0.0.1:8000/api/connections/youtube/callback",
        mock_mode: bool = False,
        on_tokens_refreshed: Optional[Callable[[Dict[str, Any]], None]] = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ):
        if mock_mode:
            assert_mock_allowed("NativeYouTubePublisher")

        self.client_id = (client_id if client_id is not None else (os.getenv("YOUTUBE_CLIENT_ID") or "")).strip()
        self.client_secret = (client_secret if client_secret is not None else (os.getenv("YOUTUBE_CLIENT_SECRET") or "")).strip()
        self.access_token = (access_token if access_token is not None else (os.getenv("YOUTUBE_ACCESS_TOKEN") or "")).strip()
        self.refresh_token = (refresh_token if refresh_token is not None else (os.getenv("YOUTUBE_REFRESH_TOKEN") or "")).strip()
        self.redirect_uri = redirect_uri
        self.mock_mode = mock_mode
        self.on_tokens_refreshed = on_tokens_refreshed
        self.chunk_size = max(256 * 1024, chunk_size)
        self._mock_videos: Dict[str, Dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        if self.mock_mode:
            return True
        return bool(self.client_id and self.client_secret and (self.access_token or self.refresh_token))

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Genera el enlace de consentimiento OAuth 2.0 para el propietario."""
        if not self.client_id:
            raise RuntimeError("Falta YOUTUBE_CLIENT_ID para iniciar OAuth 2.0.")
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
        }
        if state:
            params["state"] = state
        return f"{YOUTUBE_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Intercambia el código de autorización por tokens de acceso y renovación."""
        if self.mock_mode:
            assert_mock_allowed("NativeYouTubePublisher.exchange_code")
            return {
                "access_token": "mock-yt-access-token",
                "refresh_token": "mock-yt-refresh-token",
                "expires_in": 3600,
                "token_type": "Bearer",
                "scope": " ".join(DEFAULT_SCOPES),
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
            raise RuntimeError(redact_secrets(f"Error al canjear código OAuth (HTTP {resp.status_code})."))

        data = resp.json()
        self.access_token = data.get("access_token", "")
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        return data

    def refresh_access_token(self) -> str:
        """Renueva el token de acceso expirado utilizando el refresh_token y notifica al almacén cifrado."""
        if self.mock_mode:
            assert_mock_allowed("NativeYouTubePublisher.refresh_access_token")
            self.access_token = f"refreshed-mock-token-{int(time.time())}"
            if self.on_tokens_refreshed:
                self.on_tokens_refreshed({"access_token": self.access_token, "expires_in": 3600})
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
            raise RuntimeError(redact_secrets(f"Error al renovar token de YouTube (HTTP {resp.status_code})."))

        data = resp.json()
        self.access_token = data.get("access_token", "")
        if self.on_tokens_refreshed and self.access_token:
            self.on_tokens_refreshed({
                "access_token": self.access_token,
                "expires_in": data.get("expires_in"),
                "scopes": data.get("scope", "").split() if data.get("scope") else None,
            })
        return self.access_token

    def _get_authenticated_headers(self) -> Dict[str, str]:
        if not self.access_token and self.refresh_token:
            self.refresh_access_token()
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

    def _request_with_refresh(self, method: str, url: str, **kwargs) -> requests.Response:
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(self._get_authenticated_headers())
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401 and self.refresh_token:
            self.refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        return resp

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
            resp = self._request_with_refresh(
                "GET",
                f"{YOUTUBE_API_BASE}/channels?part=snippet,statistics&mine=true",
                timeout=10.0,
            )
            if resp.status_code != 200:
                logger.warning("Error al consultar canal de YouTube: HTTP %d", resp.status_code)
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
            logger.error("Error al obtener perfil de YouTube: %s", redact_secrets(str(e)))
            return []

    def verify_account(self, profile: str, platform: str) -> bool:
        if platform.lower() != "youtube":
            return False
        if self.mock_mode:
            return True
        return len(self.list_profiles()) > 0

    def probe_resumable_upload(self, session_uri: str, total_size: int) -> Dict[str, Any]:
        """
        Consulta el estado de una sesión resumible de Google antes de reintentar una subida.
        Evita re-subir videos si la sesión anterior ya se completó o permite retomar desde el último byte.
        """
        headers = {
            "Content-Length": "0",
            "Content-Range": f"bytes */{total_size}",
        }
        resp = requests.put(session_uri, headers=headers, timeout=30.0)
        if resp.status_code in (200, 201):
            data = resp.json()
            return {"state": "completed", "video_id": data.get("id"), "payload": data}
        if resp.status_code == 308:
            range_hdr = resp.headers.get("Range", "")
            match = _RANGE_RE.search(range_hdr)
            next_offset = int(match.group(1)) + 1 if match else 0
            return {"state": "incomplete", "next_offset": next_offset}
        if resp.status_code in (404, 410):
            return {"state": "expired", "status_code": resp.status_code}
        return {"state": "error", "status_code": resp.status_code}

    def _verify_uploaded_video(self, video_id: str, fallback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Confirma el video recién subido consultando videos.list en la API oficial."""
        try:
            resp = self._request_with_refresh(
                "GET",
                f"{YOUTUBE_API_BASE}/videos?part=snippet,status,processingDetails&id={video_id}",
                timeout=15.0,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    return items[0]
        except Exception as e:
            logger.warning("Verificación post-subida diferida para %s: %s", video_id, redact_secrets(str(e)))
        return fallback_payload

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
        existing_session_uri: Optional[str] = None,
        on_session_created: Optional[Callable[[str], None]] = None,
        on_progress: Optional[Callable[[int], None]] = None,
    ) -> PublishResult:
        """
        Sube el video a YouTube mediante subida resumible por bloques con recuperación parcial.
        Siempre envía explícitamente status.privacyStatus (por defecto 'private').
        """
        req_id = f"yt-native-{uuid.uuid4()}"
        clean_visibility = (visibility or "private").strip().lower()
        if clean_visibility not in ("private", "unlisted", "public"):
            clean_visibility = "private"

        if self.mock_mode:
            assert_mock_allowed("NativeYouTubePublisher.submit_post")
            video_id = f"yt_mock_{uuid.uuid4().hex[:11]}"
            video_info = {
                "id": video_id,
                "title": title[:100],
                "description": description or "",
                "status": "PUBLISHED" if not scheduled_date_iso else "SCHEDULED",
                "privacy": clean_visibility,
                "channel_id": "UC_MOCK_CHANNEL_ID",
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
                remote_url=video_info["url"],
                raw_payload={
                    "mock": True,
                    "videoId": video_id,
                    "channelId": "UC_MOCK_CHANNEL_ID",
                    "privacyStatus": clean_visibility,
                },
            )

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="YouTube nativo no está configurado (faltan credenciales OAuth autorizadas).",
            )

        path = Path(video_path)
        if not path.is_file():
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=f"Archivo no encontrado: {video_path}",
            )

        total_size = path.stat().st_size
        if total_size <= 0:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="El archivo de video está vacío (0 bytes).",
            )

        # 1. Recuperación resumible: comprobar si ya existía una sesión previa antes de abrir una nueva
        upload_url: Optional[str] = None
        offset = 0

        if existing_session_uri:
            try:
                probe = self.probe_resumable_upload(existing_session_uri, total_size)
                if probe["state"] == "completed" and probe.get("video_id"):
                    vid = probe["video_id"]
                    verified = self._verify_uploaded_video(vid, probe.get("payload") or {})
                    return self._build_success_result(req_id, vid, verified, clean_visibility, scheduled_date_iso)
                elif probe["state"] == "incomplete":
                    upload_url = existing_session_uri
                    offset = int(probe.get("next_offset", 0))
                    logger.info("Reanudando subida resumible de YouTube desde byte %d/%d", offset, total_size)
            except Exception as e:
                logger.warning("No se pudo sondear sesión resumible previa: %s", redact_secrets(str(e)))

        # 2. Iniciar nueva sesión resumible si no había una activa/recuperable
        if not upload_url:
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
                "privacyStatus": clean_visibility,
                "selfDeclaredMadeForKids": False,
            }
            if scheduled_date_iso and clean_visibility == "private":
                status_dict["publishAt"] = scheduled_date_iso

            metadata = {"snippet": snippet, "status": status_dict}

            try:
                init_resp = self._request_with_refresh(
                    "POST",
                    f"{YOUTUBE_UPLOAD_URL}?uploadType=resumable&part=snippet,status",
                    headers={
                        "X-Upload-Content-Type": "video/mp4",
                        "X-Upload-Content-Length": str(total_size),
                        "Content-Type": "application/json; charset=UTF-8",
                    },
                    data=json.dumps(metadata),
                    timeout=30.0,
                )
                if init_resp.status_code not in (200, 201):
                    return PublishResult(
                        ok=False,
                        request_id=req_id,
                        status="FAILED",
                        error=redact_secrets(
                            f"YouTube rechazó inicio de subida resumible: HTTP {init_resp.status_code}"
                        ),
                    )
                upload_url = init_resp.headers.get("Location")
                if not upload_url:
                    return PublishResult(
                        ok=False,
                        request_id=req_id,
                        status="FAILED",
                        error="YouTube no devolvió cabecera Location para la sesión resumible.",
                    )
                if on_session_created:
                    on_session_created(upload_url)
            except Exception as e:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error=redact_secrets(f"Excepción al iniciar sesión resumible de YouTube: {e}"),
                )

        # 3. Transmisión por bloques (chunks) con Content-Range
        try:
            with open(path, "rb") as video_file:
                if offset > 0:
                    video_file.seek(offset)

                while offset < total_size:
                    chunk = video_file.read(self.chunk_size)
                    if not chunk:
                        break
                    chunk_len = len(chunk)
                    end_byte = offset + chunk_len - 1
                    chunk_headers = {
                        "Content-Type": "video/mp4",
                        "Content-Length": str(chunk_len),
                        "Content-Range": f"bytes {offset}-{end_byte}/{total_size}",
                    }
                    put_resp = requests.put(
                        upload_url,
                        headers=chunk_headers,
                        data=chunk,
                        timeout=120.0,
                    )

                    if put_resp.status_code == 308:
                        range_hdr = put_resp.headers.get("Range", "")
                        match = _RANGE_RE.search(range_hdr)
                        offset = int(match.group(1)) + 1 if match else (end_byte + 1)
                        if on_progress:
                            on_progress(offset)
                        continue

                    if put_resp.status_code in (200, 201):
                        res_data = put_resp.json()
                        video_id = res_data.get("id")
                        if not video_id:
                            return PublishResult(
                                ok=False,
                                request_id=req_id,
                                status="FAILED",
                                error="YouTube completó la subida pero no devolvió video_id.",
                            )
                        if on_progress:
                            on_progress(total_size)
                        verified = self._verify_uploaded_video(video_id, res_data)
                        return self._build_success_result(
                            req_id, video_id, verified, clean_visibility, scheduled_date_iso
                        )

                    # Error recuperable (5xx / 429) conserva upload_session_uri para reanudar luego
                    return PublishResult(
                        ok=False,
                        request_id=req_id,
                        status="FAILED",
                        error=redact_secrets(
                            f"YouTube interrumpió subida en byte {offset}/{total_size}: HTTP {put_resp.status_code}"
                        ),
                    )

            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="Flujo de subida resumible finalizó sin confirmación HTTP 200/201 de YouTube.",
            )
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=redact_secrets(f"Interrupción de red en subida resumible (byte {offset}): {e}"),
            )

    def _build_success_result(
        self,
        req_id: str,
        video_id: str,
        video_payload: Dict[str, Any],
        requested_privacy: str,
        scheduled_date_iso: Optional[str],
    ) -> PublishResult:
        snippet = video_payload.get("snippet", {})
        status_obj = video_payload.get("status", {})
        proc_obj = video_payload.get("processingDetails", {})

        channel_id = snippet.get("channelId")
        privacy = status_obj.get("privacyStatus") or requested_privacy
        upload_status = (status_obj.get("uploadStatus") or "").lower()
        proc_status = (proc_obj.get("processingStatus") or "").lower()

        if upload_status in ("rejected", "failed") or proc_status == "failed":
            return PublishResult(
                ok=False,
                request_id=req_id,
                job_id=video_id,
                remote_post_id=video_id,
                remote_url=f"https://www.youtube.com/shorts/{video_id}",
                status="FAILED",
                error=f"YouTube rechazó el video ({status_obj.get('rejectionReason') or upload_status}).",
                raw_payload=video_payload,
            )

        if proc_status == "processing":
            final_status = "PROCESSING"
        else:
            final_status = "PUBLISHED" if not scheduled_date_iso else "SCHEDULED"

        return PublishResult(
            ok=True,
            request_id=req_id,
            job_id=video_id,
            status=final_status,
            remote_post_id=video_id,
            remote_url=f"https://www.youtube.com/shorts/{video_id}",
            raw_payload={
                "videoId": video_id,
                "channelId": channel_id,
                "privacyStatus": privacy,
                "uploadStatus": upload_status or "uploaded",
                "processingStatus": proc_status or "succeeded",
                "video": video_payload,
            },
        )

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """
        Consulta el estado real del video en YouTube usando su video_id real.
        """
        if self.mock_mode:
            video = self._mock_videos.get(request_id)
            if video:
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=video["id"],
                    status=video["status"],
                    remote_post_id=video["id"],
                    remote_url=video["url"],
                    raw_payload=video,
                )
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Video no encontrado")

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No configurado")

        # Si solo tenemos un request_id local (yt-native-...) sin video_id real, no consultar como ID de YouTube
        if request_id.startswith("yt-native-"):
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="PROCESSING",
                error="Sin video_id remoto confirmado aún; use probe_resumable_upload sobre upload_session_uri.",
            )

        try:
            resp = self._request_with_refresh(
                "GET",
                f"{YOUTUBE_API_BASE}/videos?part=snippet,status,processingDetails,statistics&id={request_id}",
                timeout=10.0,
            )
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    v = items[0]
                    vid = v.get("id", request_id)
                    status_obj = v.get("status", {})
                    proc_obj = v.get("processingDetails", {})
                    upload_st = (status_obj.get("uploadStatus") or "").lower()
                    proc_st = (proc_obj.get("processingStatus") or "").lower()

                    if upload_st in ("rejected", "failed") or proc_st == "failed":
                        mapped = "FAILED"
                    elif proc_st == "processing":
                        mapped = "PROCESSING"
                    else:
                        mapped = "PUBLISHED"

                    return PublishResult(
                        ok=(mapped != "FAILED"),
                        request_id=request_id,
                        job_id=vid,
                        status=mapped,
                        remote_post_id=vid,
                        remote_url=f"https://www.youtube.com/shorts/{vid}",
                        raw_payload=v,
                    )
                return PublishResult(
                    ok=False,
                    request_id=request_id,
                    status="FAILED",
                    error="Video no encontrado en YouTube con el ID indicado.",
                )
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="FAILED",
                error=redact_secrets(f"YouTube videos.list HTTP {resp.status_code}"),
            )
        except Exception as e:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=redact_secrets(str(e)))

    def fetch_video_statistics(self, video_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Consulta estadísticas reales oficiales (viewCount, likeCount, commentCount)
        para una lista de video_ids de YouTube.
        """
        clean_ids = [vid.strip() for vid in video_ids if vid and not vid.startswith("yt-native-")]
        if not clean_ids or not self.is_configured or self.mock_mode:
            return {}

        stats_map: Dict[str, Dict[str, Any]] = {}
        try:
            # YouTube Data API admite hasta 50 IDs por petición
            for i in range(0, len(clean_ids), 50):
                batch = ",".join(clean_ids[i : i + 50])
                resp = self._request_with_refresh(
                    "GET",
                    f"{YOUTUBE_API_BASE}/videos?part=statistics,snippet,status&id={batch}",
                    timeout=10.0,
                )
                if resp.status_code != 200:
                    logger.warning("YouTube statistics HTTP %d", resp.status_code)
                    continue
                for item in resp.json().get("items", []):
                    vid = item.get("id")
                    st = item.get("statistics", {})
                    if vid:
                        stats_map[vid] = {
                            "views": int(st["viewCount"]) if "viewCount" in st else None,
                            "likes": int(st["likeCount"]) if "likeCount" in st else None,
                            "comments": int(st["commentCount"]) if "commentCount" in st else None,
                            "privacy_status": item.get("status", {}).get("privacyStatus"),
                            "channel_id": item.get("snippet", {}).get("channelId"),
                            "fetched_at": datetime.now(timezone.utc).isoformat(),
                        }
        except Exception as e:
            logger.error("Error al consultar estadísticas reales de YouTube: %s", redact_secrets(str(e)))

        return stats_map

    def cancel_scheduled(self, job_id: str) -> bool:
        """
        Cancelación segura: NUNCA invoca videos.delete sobre YouTube para evitar
        eliminar accidentalmente un video ya subido. Solo permite cancelar tareas
        locales que aún no tienen un video remoto publicado.
        """
        if self.mock_mode:
            if job_id in self._mock_videos:
                # En mock solo cancela si no estaba ya publicado
                if self._mock_videos[job_id].get("status") == "SCHEDULED":
                    self._mock_videos[job_id]["status"] = "CANCELLED"
                    return True
            return False

        # Nunca ejecutar DELETE /youtube/v3/videos de forma automática al cancelar un trabajo
        logger.warning(
            "Cancelación remota destructiva bloqueada para YouTube (%s): "
            "no se invoca videos.delete sobre contenido ya subido.",
            job_id,
        )
        return False

    def fetch_comments(self, video_id: str, max_results: int = 20) -> List[Dict[str, Any]]:
        """Obtiene comentarios reales del video mediante YouTube Data API v3."""
        if self.mock_mode:
            assert_mock_allowed("NativeYouTubePublisher.fetch_comments")
            return [
                {
                    "comment_id": "mock-comment-1",
                    "author": "TechFan2026",
                    "author_id": "UC_MOCK_AUTHOR_1",
                    "text": "Excelente clip! Muy bien explicado.",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                }
            ]

        if not self.is_configured:
            return []

        try:
            url = f"{YOUTUBE_API_BASE}/commentThreads?part=snippet&videoId={video_id}&maxResults={max_results}"
            resp = self._request_with_refresh("GET", url, timeout=10.0)
            if resp.status_code != 200:
                logger.warning("No se pudieron leer comentarios para video %s: HTTP %d", video_id, resp.status_code)
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
            logger.error("Error al leer comentarios de YouTube: %s", redact_secrets(str(e)))
            return []

    def reply_to_comment(self, comment_id: str, text: str) -> bool:
        """Responde a un comentario de forma oficial mediante YouTube Data API v3."""
        if self.mock_mode:
            assert_mock_allowed("NativeYouTubePublisher.reply_to_comment")
            return True

        if not self.is_configured:
            return False

        try:
            body = {
                "snippet": {
                    "parentId": comment_id,
                    "textOriginal": text,
                }
            }
            resp = self._request_with_refresh(
                "POST",
                f"{YOUTUBE_API_BASE}/comments?part=snippet",
                headers={"Content-Type": "application/json; charset=UTF-8"},
                data=json.dumps(body),
                timeout=10.0,
            )
            return resp.status_code in (200, 201)
        except Exception as e:
            logger.error("Error al responder comentario en YouTube: %s", redact_secrets(str(e)))
            return False
