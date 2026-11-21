"""Conector nativo directo para TikTok Content Posting API v2 con OAuth 2.0 y subida por bloques."""
from __future__ import annotations

import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import requests

from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.security import assert_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
TIKTOK_USER_INFO_URL = "https://open.tiktokapis.com/v2/user/info/"
TIKTOK_DIRECT_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
TIKTOK_INBOX_INIT_URL = "https://open.tiktokapis.com/v2/post/publish/inbox/video/init/"
TIKTOK_STATUS_URL = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"

# TikTok Content Posting API exige bloques entre 5 MB y 64 MB (10 MB por defecto; el último puede ser menor si hay >1 chunk, o todo el archivo si <=64 MB)
DEFAULT_TIKTOK_CHUNK_SIZE = 10 * 1024 * 1024
DEFAULT_TIKTOK_SCOPES = ["user.info.basic", "video.upload", "video.publish"]


class NativeTikTokPublisher(BasePublisher):
    """
    Publicador nativo de TikTok mediante OAuth 2.0 y Content Posting API v2.
    Mantiene privacidad SELF_ONLY por defecto y registra envíos a bandeja/borrador
    como ACTION_REQUIRED (nunca PUBLISHED).
    """

    def __init__(
        self,
        client_key: Optional[str] = None,
        client_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        refresh_token: Optional[str] = None,
        redirect_uri: str = "http://127.0.0.1:8000/api/connections/tiktok/callback",
        mock_mode: bool = False,
        on_tokens_refreshed: Optional[Callable[[Dict[str, Any]], None]] = None,
        chunk_size: int = DEFAULT_TIKTOK_CHUNK_SIZE,
    ):
        if mock_mode:
            assert_mock_allowed("NativeTikTokPublisher")

        self.client_key = (client_key if client_key is not None else (os.getenv("TIKTOK_CLIENT_KEY") or "")).strip()
        self.client_secret = (
            client_secret if client_secret is not None else (os.getenv("TIKTOK_CLIENT_SECRET") or "")
        ).strip()
        self.access_token = (
            access_token if access_token is not None else (os.getenv("TIKTOK_ACCESS_TOKEN") or "")
        ).strip()
        self.refresh_token = (
            refresh_token if refresh_token is not None else (os.getenv("TIKTOK_REFRESH_TOKEN") or "")
        ).strip()
        self.redirect_uri = redirect_uri
        self.mock_mode = mock_mode
        self.on_tokens_refreshed = on_tokens_refreshed
        self.chunk_size = max(5 * 1024 * 1024, min(64 * 1024 * 1024, chunk_size))
        self._mock_posts: Dict[str, Dict[str, Any]] = {}

    @property
    def is_configured(self) -> bool:
        if self.mock_mode:
            return True
        return bool(self.client_key and self.client_secret and (self.access_token or self.refresh_token))

    def get_authorization_url(self, state: Optional[str] = None, scopes: Optional[List[str]] = None) -> str:
        """Genera la URL oficial de autorización OAuth 2.0 de TikTok."""
        if not self.client_key:
            raise RuntimeError("Falta client_key de TikTok para iniciar OAuth 2.0.")
        params = {
            "client_key": self.client_key,
            "scope": ",".join(scopes or DEFAULT_TIKTOK_SCOPES),
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{TIKTOK_AUTH_URL}?{urlencode(params)}"

    def exchange_code(self, code: str) -> Dict[str, Any]:
        """Intercambia el authorization code por access_token y refresh_token de TikTok."""
        if self.mock_mode:
            assert_mock_allowed("NativeTikTokPublisher.exchange_code")
            return {
                "access_token": "mock-tt-access-token",
                "refresh_token": "mock-tt-refresh-token",
                "expires_in": 86400,
                "refresh_expires_in": 31536000,
                "open_id": "mock_open_id",
                "scope": ",".join(DEFAULT_TIKTOK_SCOPES),
                "token_type": "Bearer",
            }

        payload = {
            "client_key": self.client_key,
            "client_secret": self.client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
        }
        resp = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=payload,
            timeout=15.0,
        )
        data = resp.json() if resp.headers.get("Content-Type", "").startswith("application/json") or resp.text.startswith("{") else {}
        if resp.status_code != 200 or data.get("error"):
            err = data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}"
            raise RuntimeError(redact_secrets(f"Error al canjear código OAuth de TikTok: {err}"))

        self.access_token = data.get("access_token", "")
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        return data

    def refresh_access_token(self) -> str:
        """Renueva el access_token de TikTok usando refresh_token y notifica al almacén cifrado."""
        if self.mock_mode:
            assert_mock_allowed("NativeTikTokPublisher.refresh_access_token")
            self.access_token = f"refreshed-tt-mock-{int(time.time())}"
            if self.on_tokens_refreshed:
                self.on_tokens_refreshed({"access_token": self.access_token, "expires_in": 86400})
            return self.access_token

        if not (self.refresh_token and self.client_key and self.client_secret):
            raise RuntimeError("Faltan client_key, client_secret o refresh_token para renovar TikTok.")

        resp = requests.post(
            TIKTOK_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_key": self.client_key,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=15.0,
        )
        data = resp.json() if resp.text.startswith("{") else {}
        if resp.status_code != 200 or data.get("error"):
            err = data.get("error_description") or data.get("error") or f"HTTP {resp.status_code}"
            raise RuntimeError(redact_secrets(f"Error al renovar token de TikTok: {err}"))

        self.access_token = data.get("access_token", "")
        if data.get("refresh_token"):
            self.refresh_token = data["refresh_token"]
        if self.on_tokens_refreshed and self.access_token:
            raw_scope = data.get("scope", "")
            scopes = [s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()] if raw_scope else None
            self.on_tokens_refreshed({
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "expires_in": data.get("expires_in"),
                "scopes": scopes,
            })
        return self.access_token

    def _request_with_refresh(self, method: str, url: str, **kwargs) -> requests.Response:
        if not self.access_token and self.refresh_token:
            self.refresh_access_token()
        headers = dict(kwargs.pop("headers", None) or {})
        headers["Authorization"] = f"Bearer {self.access_token}"
        resp = requests.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401 and self.refresh_token:
            self.refresh_access_token()
            headers["Authorization"] = f"Bearer {self.access_token}"
            resp = requests.request(method, url, headers=headers, **kwargs)
        return resp

    def list_profiles(self) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return [{
                "id": "mock_open_id",
                "username": "tiktok_owner",
                "connected_platforms": ["tiktok"],
                "reconnect_platforms": [],
            }]
        if not self.is_configured:
            return []
        try:
            resp = self._request_with_refresh(
                "GET",
                f"{TIKTOK_USER_INFO_URL}?fields=open_id,union_id,avatar_url,display_name",
                timeout=10.0,
            )
            if resp.status_code != 200:
                return []
            user = resp.json().get("data", {}).get("user", {})
            if not user:
                return []
            return [{
                "id": user.get("open_id"),
                "username": user.get("display_name", "TikTok User"),
                "connected_platforms": ["tiktok"],
                "reconnect_platforms": [],
                "raw": user,
            }]
        except Exception as e:
            logger.error("Error al consultar perfil de TikTok: %s", redact_secrets(str(e)))
            return []

    def verify_account(self, profile: str, platform: str) -> bool:
        if platform.lower() != "tiktok":
            return False
        if self.mock_mode:
            return True
        return len(self.list_profiles()) > 0

    @staticmethod
    def _normalize_privacy(visibility: Optional[str]) -> str:
        val = (visibility or "SELF_ONLY").strip().upper()
        if val in ("PRIVATE", "SELF_ONLY"):
            return "SELF_ONLY"
        if val in ("PUBLIC", "PUBLIC_TO_EVERYONE"):
            return "PUBLIC_TO_EVERYONE"
        if val in ("FRIENDS", "MUTUAL_FOLLOW_FRIENDS"):
            return "MUTUAL_FOLLOW_FRIENDS"
        if val in ("FOLLOWERS", "FOLLOWER_OF_CREATOR"):
            return "FOLLOWER_OF_CREATOR"
        return "SELF_ONLY"

    def submit_post(
        self,
        video_path: str,
        title: str,
        platform: str,
        profile: str,
        description: Optional[str] = None,
        visibility: str = "SELF_ONLY",
        scheduled_date_iso: Optional[str] = None,
        timezone_str: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        output_variant_id: Optional[str] = None,
        project_id: Optional[str] = None,
        on_progress: Optional[Callable[[int], None]] = None,
        **_: Any,
    ) -> PublishResult:
        req_id = f"tt-native-{uuid.uuid4()}"
        privacy_level = self._normalize_privacy(visibility)
        caption = (description or title or "").strip()[:2200]

        if self.mock_mode:
            assert_mock_allowed("NativeTikTokPublisher.submit_post")
            pub_id = f"tt_mock_{uuid.uuid4().hex[:12]}"
            post_info = {
                "publish_id": pub_id,
                "status": "PUBLISH_COMPLETE",
                "privacy_level": privacy_level,
            }
            self._mock_posts[pub_id] = post_info
            self._mock_posts[req_id] = post_info
            return PublishResult(
                ok=True,
                request_id=req_id,
                job_id=pub_id,
                status="PUBLISHED",
                remote_post_id=pub_id,
                raw_payload={"mock": True, "publish_id": pub_id, "privacyStatus": privacy_level},
            )

        if not self.is_configured:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error="TikTok nativo no está configurado (faltan client_key, client_secret o token OAuth).",
            )

        path = Path(video_path)
        if not path.is_file():
            return PublishResult(ok=False, request_id=req_id, status="FAILED", error=f"Archivo no encontrado: {video_path}")

        total_size = path.stat().st_size
        if total_size <= 0:
            return PublishResult(ok=False, request_id=req_id, status="FAILED", error="El archivo de video está vacío.")

        # TikTok exige 1 único chunk si total_size <= 64 MB; de lo contrario chunks de 10 MB
        if total_size <= 64 * 1024 * 1024:
            chunk_size = total_size
            total_chunks = 1
        else:
            chunk_size = self.chunk_size
            total_chunks = math.ceil(total_size / chunk_size)

        direct_body = {
            "post_info": {
                "title": caption,
                "privacy_level": privacy_level,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": total_size,
                "chunk_size": chunk_size,
                "total_chunk_count": total_chunks,
            },
        }

        used_inbox_fallback = False
        try:
            init_resp = self._request_with_refresh(
                "POST",
                TIKTOK_DIRECT_INIT_URL,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                json=direct_body,
                timeout=30.0,
            )
            init_json = init_resp.json() if init_resp.text.startswith("{") else {}
            err_code = (init_json.get("error") or {}).get("code", "ok")

            # Si la app aún no tiene auditoría para direct post (unaudited_client_can_only_post_to_private_accounts o scope_not_authorized), intentar inbox draft
            if init_resp.status_code != 200 or (err_code and err_code != "ok"):
                if err_code in ("unaudited_client_can_only_post_to_private_accounts", "scope_not_authorized", "spam_risk_too_many_pending_share"):
                    inbox_body = {"source_info": direct_body["source_info"]}
                    init_resp = self._request_with_refresh(
                        "POST",
                        TIKTOK_INBOX_INIT_URL,
                        headers={"Content-Type": "application/json; charset=UTF-8"},
                        json=inbox_body,
                        timeout=30.0,
                    )
                    init_json = init_resp.json() if init_resp.text.startswith("{") else {}
                    err_code = (init_json.get("error") or {}).get("code", "ok")
                    if init_resp.status_code == 200 and err_code == "ok":
                        used_inbox_fallback = True
                    else:
                        msg = (init_json.get("error") or {}).get("message") or f"HTTP {init_resp.status_code}"
                        return PublishResult(
                            ok=False,
                            request_id=req_id,
                            status="FAILED",
                            error=redact_secrets(f"TikTok rechazó inicialización ({err_code}): {msg}"),
                        )
                else:
                    msg = (init_json.get("error") or {}).get("message") or f"HTTP {init_resp.status_code}"
                    return PublishResult(
                        ok=False,
                        request_id=req_id,
                        status="FAILED",
                        error=redact_secrets(f"TikTok error ({err_code}): {msg}"),
                    )

            data_obj = init_json.get("data") or {}
            publish_id = data_obj.get("publish_id")
            upload_url = data_obj.get("upload_url")
            if not publish_id or not upload_url:
                return PublishResult(
                    ok=False,
                    request_id=req_id,
                    status="FAILED",
                    error="TikTok no devolvió publish_id o upload_url válidos.",
                )

            # Subir chunks al upload_url firmado
            offset = 0
            with open(path, "rb") as f:
                while offset < total_size:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    end_byte = offset + len(chunk) - 1
                    put_resp = requests.put(
                        upload_url,
                        headers={
                            "Content-Type": "video/mp4",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end_byte}/{total_size}",
                        },
                        data=chunk,
                        timeout=120.0,
                    )
                    if put_resp.status_code not in (200, 201, 206):
                        return PublishResult(
                            ok=False,
                            request_id=req_id,
                            job_id=publish_id,
                            remote_post_id=publish_id,
                            status="FAILED",
                            error=redact_secrets(
                                f"Fallo subiendo chunk a TikTok en byte {offset}/{total_size}: HTTP {put_resp.status_code}"
                            ),
                        )
                    offset = end_byte + 1
                    if on_progress:
                        on_progress(offset)

            if used_inbox_fallback:
                return PublishResult(
                    ok=True,
                    request_id=req_id,
                    job_id=publish_id,
                    remote_post_id=publish_id,
                    status="ACTION_REQUIRED",
                    raw_payload={
                        "publish_id": publish_id,
                        "fallback_to_inbox": True,
                        "privacyStatus": "INBOX_DRAFT",
                    },
                )

            return self.get_status(publish_id)
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=req_id,
                status="FAILED",
                error=redact_secrets(f"Excepción publicando en TikTok: {e}"),
            )

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """Consulta el estado real de publicación en TikTok usando publish_id."""
        if self.mock_mode:
            post = self._mock_posts.get(request_id)
            if not post:
                return PublishResult(ok=False, request_id=request_id, status="FAILED", error="Mock post no encontrado")
            return PublishResult(
                ok=True,
                request_id=request_id,
                job_id=request_id,
                remote_post_id=request_id,
                status="PUBLISHED",
                raw_payload=post,
            )

        if not self.is_configured:
            return PublishResult(ok=False, request_id=request_id, status="FAILED", error="TikTok no configurado.")

        try:
            resp = self._request_with_refresh(
                "POST",
                TIKTOK_STATUS_URL,
                headers={"Content-Type": "application/json; charset=UTF-8"},
                json={"publish_id": request_id},
                timeout=15.0,
            )
            payload = resp.json() if resp.text.startswith("{") else {}
            if resp.status_code != 200:
                return PublishResult(
                    ok=False,
                    request_id=request_id,
                    status="FAILED",
                    error=redact_secrets(f"Error consultando estado en TikTok: HTTP {resp.status_code}"),
                )

            data = payload.get("data") or {}
            tt_status = (data.get("status") or "").upper()
            if tt_status == "PUBLISH_COMPLETE":
                public_ids = data.get("publicaly_available_post_id") or []
                post_id = str(public_ids[0]) if public_ids else request_id
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=request_id,
                    remote_post_id=post_id,
                    status="PUBLISHED",
                    raw_payload=data,
                )
            if tt_status == "SEND_TO_USER_INBOX":
                return PublishResult(
                    ok=True,
                    request_id=request_id,
                    job_id=request_id,
                    remote_post_id=request_id,
                    status="ACTION_REQUIRED",
                    raw_payload={**data, "fallback_to_inbox": True},
                )
            if tt_status == "FAILED":
                return PublishResult(
                    ok=False,
                    request_id=request_id,
                    job_id=request_id,
                    remote_post_id=request_id,
                    status="FAILED",
                    error=redact_secrets(f"TikTok falló al procesar el video: {data.get('fail_reason', 'desconocido')}"),
                    raw_payload=data,
                )

            return PublishResult(
                ok=True,
                request_id=request_id,
                job_id=request_id,
                remote_post_id=request_id,
                status="PROCESSING",
                raw_payload=data,
            )
        except Exception as e:
            return PublishResult(
                ok=False,
                request_id=request_id,
                status="FAILED",
                error=redact_secrets(f"Error consultando estado en TikTok: {e}"),
            )

    def cancel_scheduled(self, job_id: str) -> bool:
        return False
