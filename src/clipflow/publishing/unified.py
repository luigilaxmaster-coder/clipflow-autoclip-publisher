"""Publicador Unificado multicanal con inyección de ConnectionService (credenciales cifradas)."""
from __future__ import annotations

import logging
import os
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.service import ConnectionService
from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.publishing.native_youtube import NativeYouTubePublisher
from clipflow.publishing.postiz import PostizPublisher
from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.security import assert_mock_allowed

logger = logging.getLogger(__name__)


class UnifiedPublisher(BasePublisher):
    """
    Enrutador central de publicación que obtiene credenciales cifradas directamente
    desde ConnectionService (SQLite + Fernet) mediante inyección de dependencias.
    """

    def __init__(
        self,
        prefer_engine: Optional[str] = None,  # "postiz" | "native" | "upload_post" | "auto"
        mock_mode: bool = False,
        connection_service: Optional[ConnectionService] = None,
        db: Optional[Session] = None,
    ):
        if mock_mode:
            assert_mock_allowed("UnifiedPublisher")

        self.prefer_engine = prefer_engine or os.getenv("PUBLISHER_ENGINE", "auto")
        self.mock_mode = mock_mode
        self.connection_service = connection_service or (ConnectionService(db) if db is not None else None)

        self.native_youtube = self._build_native_youtube()
        self.postiz = PostizPublisher(mock_mode=mock_mode)
        self.upload_post = UploadPostPublisher(mock_mode=mock_mode)

    def _build_native_youtube(self) -> NativeYouTubePublisher:
        """Instancia NativeYouTubePublisher usando las credenciales cifradas guardadas en ConnectionService."""
        if self.mock_mode:
            return NativeYouTubePublisher(mock_mode=True)

        if self.connection_service is not None:
            creds = self.connection_service.get_decrypted_credentials("youtube")
            if creds:
                def _persist_refreshed(token_data: Dict[str, Any]) -> None:
                    if self.connection_service and token_data.get("access_token"):
                        self.connection_service.update_oauth_tokens(
                            provider_id="youtube",
                            access_token=token_data["access_token"],
                            refresh_token=token_data.get("refresh_token"),
                            expires_in=token_data.get("expires_in"),
                            scopes=token_data.get("scopes"),
                            verify_now=False,
                        )

                return NativeYouTubePublisher(
                    client_id=creds.get("client_id", ""),
                    client_secret=creds.get("client_secret", ""),
                    access_token=creds.get("access_token", ""),
                    refresh_token=creds.get("refresh_token", ""),
                    mock_mode=False,
                    on_tokens_refreshed=_persist_refreshed,
                )

        # Fallback a variables de entorno si no hay registro en ConnectionService
        return NativeYouTubePublisher(mock_mode=False)

    def refresh_connections(self) -> None:
        """Recarga las credenciales cifradas más recientes desde ConnectionService."""
        self.native_youtube = self._build_native_youtube()

    def select_publisher_for_platform(self, platform: str) -> BasePublisher:
        plat = platform.lower()
        if self.mock_mode:
            if self.prefer_engine == "upload_post":
                return self.upload_post
            if self.prefer_engine == "postiz":
                return self.postiz
            if plat == "youtube":
                return self.native_youtube
            return self.postiz

        # Asegurar credenciales actualizadas desde el almacén cifrado
        if plat == "youtube" and self.connection_service is not None:
            self.native_youtube = self._build_native_youtube()

        if self.prefer_engine == "postiz" and self.postiz.is_configured:
            return self.postiz

        if plat == "youtube" and self.native_youtube.is_configured:
            return self.native_youtube

        if self.postiz.is_configured and self.postiz.verify_account("owner", plat):
            return self.postiz

        if self.upload_post.is_configured:
            return self.upload_post

        if plat == "youtube":
            return self.native_youtube
        if self.postiz.is_configured:
            return self.postiz
        return self.upload_post

    def list_profiles(self) -> List[Dict[str, Any]]:
        all_profiles: List[Dict[str, Any]] = []

        if self.connection_service is not None and not self.mock_mode:
            self.native_youtube = self._build_native_youtube()

        if self.native_youtube.is_configured:
            all_profiles.extend(self.native_youtube.list_profiles())

        if self.postiz.is_configured:
            all_profiles.extend(self.postiz.list_profiles())

        if self.upload_post.is_configured:
            all_profiles.extend(self.upload_post.list_profiles())

        if self.mock_mode and not all_profiles:
            return [
                {
                    "username": "personal_owner",
                    "connected_platforms": ["youtube", "tiktok", "instagram"],
                    "reconnect_platforms": [],
                }
            ]

        return all_profiles

    def verify_account(self, profile: str, platform: str) -> bool:
        pub = self.select_publisher_for_platform(platform)
        return pub.verify_account(profile, platform)

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
        pub = self.select_publisher_for_platform(platform)
        logger.info(
            "Despachando publicación para plataforma=%s usando motor=%s",
            platform,
            pub.__class__.__name__,
        )
        if isinstance(pub, NativeYouTubePublisher):
            return pub.submit_post(
                video_path=video_path,
                title=title,
                platform=platform,
                profile=profile,
                description=description,
                visibility=visibility,
                scheduled_date_iso=scheduled_date_iso,
                timezone_str=timezone_str,
                idempotency_key=idempotency_key,
                output_variant_id=output_variant_id,
                project_id=project_id,
                existing_session_uri=existing_session_uri,
                on_session_created=on_session_created,
                on_progress=on_progress,
            )
        return pub.submit_post(
            video_path=video_path,
            title=title,
            platform=platform,
            profile=profile,
            description=description,
            visibility=visibility,
            scheduled_date_iso=scheduled_date_iso,
            timezone_str=timezone_str,
            idempotency_key=idempotency_key,
            output_variant_id=output_variant_id,
            project_id=project_id,
        )

    def get_status(
        self,
        request_id: str,
        project_id: Optional[str] = None,
        platform: Optional[str] = None,
    ) -> PublishResult:
        plat = (platform or "").lower()
        if plat == "youtube" and (self.native_youtube.is_configured or self.mock_mode):
            return self.native_youtube.get_status(request_id, project_id)
        if request_id.startswith("yt-native-") or request_id.startswith("yt_mock_"):
            return self.native_youtube.get_status(request_id, project_id)
        if request_id.startswith("postiz-") or request_id.startswith("pz_") or (plat and self.prefer_engine == "postiz"):
            return self.postiz.get_status(request_id, project_id)
        return self.upload_post.get_status(request_id, project_id)

    def cancel_scheduled(self, job_id: str, platform: Optional[str] = None) -> bool:
        plat = (platform or "").lower()
        if plat == "youtube" or job_id.startswith("yt-native-") or job_id.startswith("yt_mock_"):
            return self.native_youtube.cancel_scheduled(job_id)
        if job_id.startswith("postiz-") or job_id.startswith("pz_") or (plat and self.prefer_engine == "postiz"):
            return self.postiz.cancel_scheduled(job_id)
        return self.upload_post.cancel_scheduled(job_id)
