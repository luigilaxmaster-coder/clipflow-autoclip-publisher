"""Adaptador desacoplado para el motor de publicación Postiz (gitroomhq/postiz-app)."""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from clipflow.publishing.base import BasePublisher, PublishResult

logger = logging.getLogger(__name__)

DEFAULT_POSTIZ_URL = "http://localhost:4007"


class PostizPublisher(BasePublisher):
    """
    Cliente REST desacoplado para interactuar con una instancia autohospedada de Postiz.
    Utiliza la API pública (/public/v1/*).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        mock_mode: bool = False,
        timeout: float = 15.0,
    ):
        self.api_key = (api_key if api_key is not None else (os.getenv("POSTIZ_API_KEY") or "")).strip()
        self.base_url = (base_url or os.getenv("POSTIZ_API_URL") or DEFAULT_POSTIZ_URL).rstrip("/")
        self.mock_mode = mock_mode
        self.timeout = timeout
        self._mock_posts: Dict[str, Dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) or self.mock_mode

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = self.api_key
            headers["x-api-key"] = self.api_key
        return headers

    def ping(self) -> bool:
        """Comprueba si el servidor Postiz está en línea."""
        if self.mock_mode:
            return True
        try:
            resp = requests.get(f"{self.base_url}/public/v1/me", headers=self._headers(), timeout=5.0)
            return resp.status_code in (200, 401, 403)
        except Exception:
            return False

    def list_profiles(self) -> List[Dict[str, Any]]:
        """
        Recupera la lista de canales/integraciones conectadas en Postiz.
        """
        if self.mock_mode:
            return [
                {
                    "username": "personal_owner",
                    "id": "mock-int-yt",
                    "name": "Personal YouTube Channel",
                    "identifier": "youtube",
                    "connected_platforms": ["youtube"],
                    "reconnect_platforms": [],
                },
                {
                    "username": "personal_owner",
                    "id": "mock-int-tt",
                    "name": "Personal TikTok",
                    "identifier": "tiktok",
                    "connected_platforms": ["tiktok"],
                    "reconnect_platforms": [],
                },
            ]

        if not self.is_configured:
            return []

        try:
            resp = requests.get(
                f"{self.base_url}/public/v1/integrations",
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code != 200:
                logger.warning("Postiz list_profiles falló: status=%d %s", resp.status_code, resp.text)
                return []

            data = resp.json()
            integrations = data if isinstance(data, list) else data.get("integrations", [])
            profiles = []
            for item in integrations:
                identifier = item.get("identifier") or item.get("name") or "unknown"
                profiles.append({
                    "id": item.get("id"),
                    "username": item.get("name") or "owner",
                    "identifier": identifier,
                    "connected_platforms": [identifier.lower()],
                    "reconnect_platforms": [],
                    "raw": item,
                })
            return profiles
        except Exception as e:
            logger.error("Error al conectar con Postiz en %s: %s", self.base_url, e)
            return []

    def verify_account(self, profile: str, platform: str) -> bool:
        """Verifica si la plataforma especificada está conectada y activa."""
        if self.mock_mode:
            return True
        profiles = self.list_profiles()
        for p in profiles:
            if platform.lower() in [cp.lower() for cp in p.get("connected_platforms", [])]:
                return True
        return False

    def upload_media(self, file_path: str) -> Optional[str]:
        """Sube un archivo multimedia a Postiz y devuelve su identificador remoto."""
        if self.mock_mode:
            return f"mock-media-{Path(file_path).name}"

        path = Path(file_path)
        if not path.is_file():
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

        try:
            with open(path, "rb") as f:
                files = {"file": (path.name, f, "video/mp4")}
                resp = requests.post(
                    f"{self.base_url}/public/v1/upload",
                    headers={"Authorization": self.api_key} if self.api_key else {},
                    files=files,
                    timeout=120.0,
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                return data.get("id") or data.get("url") or data.get("path")
            logger.error("Error al subir multimedia a Postiz: %d %s", resp.status_code, resp.text)
            return None
        except Exception as e:
            logger.error("Excepción en Postiz upload_media: %s", e)
            return None

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
        Publica o programa una publicación en Postiz.
        """
        req_id = f"postiz-{uuid.uuid4()}"

        if self.mock_mode:
            self._mock_posts[req_id] = {
                "id": req_id,
                "platform": platform,
                "status": "PUBLISHED" if not scheduled_date_iso else "SCHEDULED",
                "remote_url": f"https://{platform}.com/postiz-mock/{req_id}",
                "title": title,
                "scheduled_at": scheduled_date_iso,
            }
            return PublishResult(
                ok=True,
                request_id=req_id,
                job_id=req_id,
                status="PUBLISHED" if not scheduled_date_iso else "QUEUED",
                remote_post_id=f"{platform}_{req_id}",
                remote_url=f"https://{platform}.com/postiz-mock/{req_id}",
                raw_payload={"mock": True, "request_id": req_id},
            )

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="PostizPublisher no está configurado (falta POSTIZ_API_KEY).",
            )

        # 1. Encontrar la integración correspondiente
        profiles = self.list_profiles()
        matched = next(
            (p for p in profiles if platform.lower() in p.get("connected_platforms", [])),
            None,
        )
        if not matched:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=f"No hay una integración activa para '{platform}' en Postiz.",
            )

        integration_id = matched.get("id")

        # 2. Subir video si existe
        media_id = None
        if video_path and Path(video_path).is_file():
            media_id = self.upload_media(video_path)
            if not media_id:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error="Error al subir el video a Postiz antes de publicar.",
                )

        # 3. Construir payload para /public/v1/posts
        content = f"{title}\n\n{description or ''}".strip()
        post_type = "schedule" if scheduled_date_iso else "now"

        post_body = {
            "type": post_type,
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [
                        {
                            "content": content,
                            "media": [media_id] if media_id else [],
                        }
                    ],
                }
            ],
        }
        if scheduled_date_iso:
            post_body["date"] = scheduled_date_iso

        try:
            resp = requests.post(
                f"{self.base_url}/public/v1/posts",
                headers=self._headers(),
                json=post_body,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                res_data = resp.json()
                remote_id = res_data.get("id") or req_id
                return PublishResult(
                    ok=True,
                    request_id=req_id,
                    job_id=str(remote_id),
                    status="QUEUED" if scheduled_date_iso else "PUBLISHED",
                    remote_post_id=str(remote_id),
                    remote_url=res_data.get("url"),
                    raw_payload=res_data,
                )
            else:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error=f"Postiz devolvió HTTP {resp.status_code}: {resp.text}",
                    raw_payload={"status_code": resp.status_code, "body": resp.text},
                )
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=f"Error de conexión con Postiz: {e}",
            )

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """Consulta el estado del post en Postiz."""
        if self.mock_mode:
            mock_entry = self._mock_posts.get(request_id)
            if mock_entry:
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=request_id,
                    status=mock_entry.get("status", "PUBLISHED"),
                    remote_post_id=request_id,
                    remote_url=mock_entry.get("remote_url"),
                    raw_payload=mock_entry,
                )
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Post no encontrado")

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No configurado")

        try:
            resp = requests.get(
                f"{self.base_url}/public/v1/posts/{request_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                state = data.get("state") or data.get("status") or "PUBLISHED"
                status_mapped = "PUBLISHED" if state in ("published", "completed", "success") else "PROCESSING"
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=request_id,
                    status=status_mapped,
                    remote_post_id=data.get("id"),
                    remote_url=data.get("url"),
                    raw_payload=data,
                )
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=resp.text)
        except Exception as e:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=str(e))

    def cancel_scheduled(self, job_id: str) -> bool:
        """Cancela un post programado en Postiz."""
        if self.mock_mode:
            if job_id in self._mock_posts:
                self._mock_posts[job_id]["status"] = "CANCELLED"
                return True
            return False

        if not self.is_configured:
            return False

        try:
            resp = requests.delete(
                f"{self.base_url}/public/v1/posts/{job_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error("Error al cancelar post %s en Postiz: %s", job_id, e)
            return False
