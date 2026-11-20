"""Adaptador desacoplado para el motor de publicación Postiz (gitroomhq/postiz-app, Public API v1)."""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.security import assert_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)

DEFAULT_POSTIZ_URL = "http://127.0.0.1:4007"


class PostizPublisher(BasePublisher):
    """
    Cliente REST desacoplado para interactuar con una instancia autohospedada de Postiz.
    Implementa los contratos reales de Public API (/public/v1/*) y utiliza
    identificadores remotos persistidos (postId) para consultas y cancelaciones.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        mock_mode: bool = False,
        timeout: float = 15.0,
    ):
        if mock_mode:
            assert_mock_allowed("PostizPublisher")

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
        return headers

    def ping(self) -> bool:
        """Comprueba si el servidor Postiz responde con autenticación válida."""
        if self.mock_mode:
            return True
        if not self.api_key:
            return False
        try:
            resp = requests.get(
                f"{self.base_url}/public/v1/integrations",
                headers=self._headers(),
                timeout=5.0,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def list_profiles(self) -> List[Dict[str, Any]]:
        """Recupera la lista de canales/integraciones activas en Postiz."""
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
                logger.warning("Postiz list_profiles falló: HTTP %d", resp.status_code)
                return []

            data = resp.json()
            integrations = data if isinstance(data, list) else data.get("integrations", [])
            profiles = []
            for item in integrations:
                if item.get("disabled"):
                    continue
                identifier = (item.get("identifier") or item.get("name") or "unknown").lower()
                profiles.append({
                    "id": item.get("id"),
                    "username": item.get("name") or "owner",
                    "identifier": identifier,
                    "connected_platforms": [identifier],
                    "reconnect_platforms": [],
                    "raw": item,
                })
            return profiles
        except Exception as e:
            logger.error("Error al conectar con Postiz en %s: %s", self.base_url, redact_secrets(str(e)))
            return []

    def verify_account(self, profile: str, platform: str) -> bool:
        """Verifica si la plataforma especificada está conectada y activa en Postiz."""
        if self.mock_mode:
            return True
        profiles = self.list_profiles()
        return any(platform.lower() in [cp.lower() for cp in p.get("connected_platforms", [])] for p in profiles)

    def upload_media(self, file_path: str) -> Optional[Dict[str, str]]:
        """Sube un archivo multimedia a Postiz y devuelve {id, path} para el payload de posts."""
        if self.mock_mode:
            name = Path(file_path).name
            return {"id": f"mock-media-{name}", "path": f"/uploads/{name}"}

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
                    timeout=180.0,
                )
            if resp.status_code in (200, 201):
                data = resp.json()
                media_id = data.get("id")
                media_path = data.get("path") or data.get("url")
                if media_id or media_path:
                    return {"id": str(media_id or ""), "path": str(media_path or "")}
            logger.error("Error al subir multimedia a Postiz: HTTP %d", resp.status_code)
            return None
        except Exception as e:
            logger.error("Excepción en Postiz upload_media: %s", redact_secrets(str(e)))
            return None

    def _extract_remote_post_id(self, res_data: Any) -> Optional[str]:
        """Extrae el postId remoto real del formato devuelto por Postiz (/public/v1/posts)."""
        if isinstance(res_data, list) and res_data:
            first = res_data[0]
            if isinstance(first, dict):
                return str(first.get("postId") or first.get("id") or "") or None
        if isinstance(res_data, dict):
            if res_data.get("postId"):
                return str(res_data["postId"])
            if res_data.get("id"):
                return str(res_data["id"])
            posts = res_data.get("posts")
            if isinstance(posts, list) and posts and isinstance(posts[0], dict):
                return str(posts[0].get("postId") or posts[0].get("id") or "") or None
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
        """Publica o programa una publicación en Postiz conservando el postId remoto real."""
        req_id = f"postiz-{uuid.uuid4()}"

        if self.mock_mode:
            assert_mock_allowed("PostizPublisher.submit_post")
            remote_id = f"pz_{uuid.uuid4().hex[:12]}"
            entry = {
                "id": remote_id,
                "platform": platform,
                "status": "PUBLISHED" if not scheduled_date_iso else "SCHEDULED",
                "remote_url": f"https://{platform}.com/postiz-mock/{remote_id}",
                "title": title,
                "scheduled_at": scheduled_date_iso,
            }
            self._mock_posts[remote_id] = entry
            self._mock_posts[req_id] = entry
            return PublishResult(
                ok=True,
                request_id=req_id,
                job_id=remote_id,
                status="PUBLISHED" if not scheduled_date_iso else "SCHEDULED",
                remote_post_id=remote_id,
                remote_url=entry["remote_url"],
                raw_payload={"mock": True, "postId": remote_id},
            )

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="PostizPublisher no está configurado (falta POSTIZ_API_KEY).",
            )

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
        platform_identifier = matched.get("identifier") or platform.lower()

        media_obj = None
        if video_path and Path(video_path).is_file():
            media_obj = self.upload_media(video_path)
            if not media_obj:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error="Error al subir el video a Postiz antes de publicar.",
                )

        content = f"{title}\n\n{description or ''}".strip()
        post_type = "schedule" if scheduled_date_iso else "now"
        post_date = scheduled_date_iso or datetime.now(timezone.utc).isoformat()

        value_block: Dict[str, Any] = {
            "content": content,
            "image": [media_obj] if media_obj else [],
        }
        settings_block: Dict[str, Any] = {"__type": platform_identifier}
        if platform_identifier == "youtube":
            settings_block["title"] = title[:100]
            settings_block["type"] = visibility if visibility in ("private", "unlisted", "public") else "private"

        post_body = {
            "type": post_type,
            "date": post_date,
            "shortLink": False,
            "tags": [],
            "posts": [
                {
                    "integration": {"id": integration_id},
                    "value": [value_block],
                    "settings": settings_block,
                }
            ],
        }

        try:
            resp = requests.post(
                f"{self.base_url}/public/v1/posts",
                headers=self._headers(),
                json=post_body,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                res_data = resp.json()
                remote_id = self._extract_remote_post_id(res_data)
                if not remote_id:
                    return PublishResult(
                        ok=False,
                        request_id=req_id,
                        status="FAILED",
                        error="Postiz aceptó la solicitud pero no devolvió un postId remoto válido.",
                        raw_payload=res_data if isinstance(res_data, dict) else {"items": res_data},
                    )
                remote_url = res_data.get("releaseURL") if isinstance(res_data, dict) else None
                return PublishResult(
                    ok=True,
                    request_id=req_id,
                    job_id=remote_id,
                    status="SCHEDULED" if scheduled_date_iso else "PROCESSING",
                    remote_post_id=remote_id,
                    remote_url=remote_url,
                    raw_payload=res_data if isinstance(res_data, dict) else {"items": res_data},
                )
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=redact_secrets(f"Postiz devolvió HTTP {resp.status_code}"),
                raw_payload={"status_code": resp.status_code},
            )
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=redact_secrets(f"Error de conexión con Postiz: {e}"),
            )

    def _map_postiz_state(self, state: str) -> str:
        st = (state or "").strip().upper()
        if st in ("PUBLISHED", "COMPLETED", "SUCCESS"):
            return "PUBLISHED"
        if st in ("ERROR", "FAILED", "REJECTED"):
            return "FAILED"
        if st in ("QUEUE", "QUEUED", "SCHEDULED"):
            return "SCHEDULED"
        if st == "DRAFT":
            return "DRAFT"
        return "PROCESSING"

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """
        Consulta el estado remoto de un post en Postiz usando su postId remoto persistido.
        Soporta consulta directa y fallback al listado /public/v1/posts por rango de fechas.
        """
        if self.mock_mode:
            mock_entry = self._mock_posts.get(request_id)
            if mock_entry:
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=mock_entry["id"],
                    status=mock_entry.get("status", "PUBLISHED"),
                    remote_post_id=mock_entry["id"],
                    remote_url=mock_entry.get("remote_url"),
                    raw_payload=mock_entry,
                )
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Post no encontrado")

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No configurado")

        if request_id.startswith("postiz-"):
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="FAILED",
                error="Se requiere el postId remoto persistido de Postiz, no un request_id local.",
            )

        try:
            # 1. Consultar rango de publicaciones recientes en /public/v1/posts (contrato estándar Postiz)
            now = datetime.now(timezone.utc)
            start_iso = (now - timedelta(days=30)).isoformat()
            end_iso = (now + timedelta(days=90)).isoformat()
            resp = requests.get(
                f"{self.base_url}/public/v1/posts",
                headers=self._headers(),
                params={"startDate": start_iso, "endDate": end_iso},
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                payload = resp.json()
                posts = payload if isinstance(payload, list) else payload.get("posts", [])
                matched = next((p for p in posts if str(p.get("id")) == str(request_id)), None)
                if matched:
                    mapped = self._map_postiz_state(matched.get("state") or matched.get("status") or "")
                    return PublishResult(
                        ok=(mapped != "FAILED"),
                        request_id=request_id,
                        job_id=str(matched.get("id")),
                        status=mapped,
                        remote_post_id=str(matched.get("id")),
                        remote_url=matched.get("releaseURL") or matched.get("url"),
                        raw_payload=matched,
                    )
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="FAILED",
                error=f"No se encontró el post remoto {request_id} en Postiz.",
            )
        except Exception as e:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error=redact_secrets(str(e)))

    def cancel_scheduled(self, job_id: str) -> bool:
        """Cancela un post programado en Postiz usando su postId remoto persistido."""
        if self.mock_mode:
            if job_id in self._mock_posts:
                self._mock_posts[job_id]["status"] = "CANCELLED"
                return True
            return False

        if not self.is_configured or not job_id or job_id.startswith("postiz-"):
            return False

        try:
            resp = requests.delete(
                f"{self.base_url}/public/v1/posts/{job_id}",
                headers=self._headers(),
                timeout=self.timeout,
            )
            return resp.status_code in (200, 204)
        except Exception as e:
            logger.error("Error al cancelar post %s en Postiz: %s", job_id, redact_secrets(str(e)))
            return False
