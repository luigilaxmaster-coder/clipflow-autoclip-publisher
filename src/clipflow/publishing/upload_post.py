"""Adaptador de publicación para Upload-Post con soporte nativo de variantes Studio."""
from __future__ import annotations

import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from clipflow.publishing.base import BasePublisher, PublishResult

logger = logging.getLogger(__name__)


class UploadPostPublisher(BasePublisher):
    def __init__(
        self,
        api_key: Optional[str] = None,
        user: Optional[str] = None,
        base_url: str = "https://api.upload-post.com",
        mock_mode: bool = False,
    ):
        self.api_key = (api_key if api_key is not None else (os.getenv("UPLOAD_POST_API_KEY") or "")).strip()
        self.user = (user if user is not None else (os.getenv("UPLOAD_POST_USER") or "default")).strip()
        self.base_url = (os.getenv("UPLOAD_POST_API_BASE") or base_url).rstrip("/")
        self.mock_mode = mock_mode
        self._mock_responses: Dict[str, Any] = {}

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) or self.mock_mode

    def list_profiles(self) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return [{
                "username": self.user or "main",
                "connected_platforms": ["youtube", "tiktok"],
                "reconnect_platforms": [],
            }]

        if not self.api_key:
            return []

        from backend.services import upload_post_publisher as up
        cfg = up.UploadPostConfig(api_key=self.api_key, user=self.user, base_url=self.base_url)
        return up.list_profiles(cfg)

    def verify_account(self, profile: str, platform: str) -> bool:
        if self.mock_mode:
            return True
        profiles = self.list_profiles()
        p = next((x for x in profiles if x.get("username") == profile), None)
        if not p:
            return False
        return platform.lower() in [cp.lower() for cp in p.get("connected_platforms", [])]

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
        Envía el video a Upload-Post.
        Mapea las opciones de privacidad para YouTube (privacyStatus) y TikTok (privacy_level).
        """
        idemp_key = idempotency_key or str(uuid.uuid4())

        if self.mock_mode:
            mock_req_id = f"mock-req-{uuid.uuid4().hex[:12]}"
            res = PublishResult(
                ok=True,
                request_id=mock_req_id,
                job_id=f"mock-job-{uuid.uuid4().hex[:8]}",
                status="REMOTE_PROCESSING",
                raw_payload={"mock": True, "request_id": mock_req_id},
            )
            self._mock_responses[mock_req_id] = {
                "status": "completed",
                "results": [
                    {
                        "platform": platform,
                        "success": True,
                        "url": f"https://mock.{platform}.com/video/{uuid.uuid4().hex[:8]}",
                        "post_id": f"post-{uuid.uuid4().hex[:8]}",
                    }
                ],
            }
            return res

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id="",
                status="FAILED",
                error="Upload-Post no está configurado (falta UPLOAD_POST_API_KEY).",
            )

        # Mapeo de parámetros extra según la plataforma
        extra: Dict[str, Any] = {}
        target_platform = platform.lower()

        if target_platform == "youtube":
            # Valores: private | unlisted | public
            yt_vis = visibility if visibility in ("private", "unlisted", "public") else "private"
            extra["privacyStatus"] = yt_vis
        elif target_platform == "tiktok":
            # Valores: SELF_ONLY | MUTUAL_FOLLOW_FRIENDS | FOLLOWER_OF_CREATOR | PUBLIC_TO_EVERYONE
            tt_vis = visibility if visibility in ("SELF_ONLY", "PUBLIC_TO_EVERYONE") else "SELF_ONLY"
            extra["privacy_level"] = tt_vis

        # Puente con AutoClip upstream si hay project_id y output_variant_id
        if project_id and output_variant_id:
            from backend.services import upload_post_publisher as up
            cfg = up.UploadPostConfig(api_key=self.api_key, user=profile or self.user, base_url=self.base_url)
            req = up.PublishRequest(
                project_id=project_id,
                clip_id=output_variant_id,
                output_variant_id=output_variant_id,
                platforms=[target_platform],
                user=profile or self.user,
                title=title,
                description=description,
                scheduled_date=scheduled_date_iso,
                timezone=timezone_str,
                extra=extra,
            )
            try:
                raw_res = up.publish_clip(req, config=cfg)
                req_id = raw_res.get("request_id") or idemp_key
                return PublishResult(
                    ok=True,
                    request_id=req_id,
                    job_id=raw_res.get("job_id"),
                    status="REMOTE_PROCESSING",
                    raw_payload=raw_res,
                )
            except Exception as e:
                logger.error("Error al publicar variante via AutoClip upstream: %s", e)
                return PublishResult(
                    ok=False,
                    request_id=idemp_key,
                    status="FAILED",
                    error=str(e),
                )

        # Subida directa del archivo MP4 final
        p_vid = Path(video_path).resolve()
        if not p_vid.is_file() or p_vid.stat().st_size == 0:
            return PublishResult(
                ok=False,
                request_id=idemp_key,
                status="FAILED",
                error=f"Archivo de video no existe o está vacío: {p_vid}",
            )

        headers = {
            "Authorization": f"Apikey {self.api_key}",
            "Idempotency-Key": idemp_key,
        }
        form: Dict[str, Any] = {
            "user": profile or self.user,
            "platform[]": [target_platform],
            "title": title,
            "async_upload": "true",
            "request_id": idemp_key,
        }
        if description:
            form["description"] = description
        if scheduled_date_iso:
            form["scheduled_date"] = scheduled_date_iso
            if timezone_str:
                form["timezone"] = timezone_str
        for k, v in extra.items():
            form[k] = v

        last_err = None
        for attempt in range(1, 4):
            try:
                with open(p_vid, "rb") as handle:
                    resp = requests.post(
                        f"{self.base_url}/api/upload",
                        headers=headers,
                        data=form,
                        files={"video": (p_vid.name, handle, "video/mp4")},
                        timeout=(45, 900),
                    )
                if resp.status_code >= 400:
                    err_msg = resp.text[:500]
                    return PublishResult(
                        ok=False,
                        request_id=idemp_key,
                        status="FAILED",
                        error=f"Error HTTP {resp.status_code} desde Upload-Post: {err_msg}",
                        raw_payload={"status_code": resp.status_code, "body": resp.text},
                    )
                payload = resp.json()
                return PublishResult(
                    ok=True,
                    request_id=payload.get("request_id") or idemp_key,
                    job_id=payload.get("job_id"),
                    status="REMOTE_PROCESSING",
                    raw_payload=payload,
                )
            except Exception as e:
                last_err = e
                logger.warning("Intento %s/3 de subida a Upload-Post falló (%s). Reintentando en 3s...", attempt, e)
                time.sleep(3)

        return PublishResult(
            ok=False,
            request_id=idemp_key,
            status="FAILED",
            error=f"Excepción en envío a Upload-Post tras 3 intentos: {last_err}",
        )

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        if self.mock_mode:
            mock_data = self._mock_responses.get(request_id)
            if not mock_data:
                return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No encontrado.")
            results = mock_data.get("results", [])
            first = results[0] if results else {}
            return PublishResult(
                ok=True,
                request_id=request_id,
                status="PUBLISHED" if mock_data.get("status") == "completed" else "REMOTE_PROCESSING",
                remote_post_id=first.get("post_id"),
                remote_url=first.get("url"),
                platform_results=results,
                raw_payload=mock_data,
            )

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="No configurado.")

        from backend.services import upload_post_publisher as up
        try:
            raw = up.get_status(request_id, project_id=project_id)
            raw_status = raw.get("status")
            results = raw.get("results") or []
            
            # Buscar si alguna plataforma cayó en borrador o inbox
            is_draft = any(r.get("fallback_to_inbox") or r.get("draft") for r in results)
            
            if raw_status == "completed":
                if is_draft:
                    status_norm = "ACTION_REQUIRED"
                else:
                    status_norm = "PUBLISHED"
            elif raw_status in ("pending", "processing", "submitted"):
                status_norm = "REMOTE_PROCESSING"
            elif raw_status in ("failed", "rejected"):
                status_norm = "FAILED"
            else:
                status_norm = "REMOTE_PROCESSING"

            # Encontrar URL y post ID
            first_url = next((r.get("url") for r in results if r.get("url")), None)
            first_post_id = next((r.get("post_id") for r in results if r.get("post_id")), None)

            return PublishResult(
                ok=status_norm == "PUBLISHED",
                request_id=request_id,
                status=status_norm,
                remote_post_id=first_post_id,
                remote_url=first_url,
                platform_results=results,
                raw_payload=raw,
            )
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="FAILED",
                error=f"Error consultando estado remoto: {e}",
            )

    def cancel_scheduled(self, job_id: str) -> bool:
        if self.mock_mode:
            return True
        if not self.is_configured:
            return False
        from backend.services import upload_post_publisher as up
        cfg = up.UploadPostConfig(api_key=self.api_key, user=self.user, base_url=self.base_url)
        try:
            up.cancel_scheduled(job_id, config=cfg)
            return True
        except Exception as e:
            logger.error("Error cancelando publicación programada: %s", e)
            return False
