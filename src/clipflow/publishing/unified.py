"""Publicador Unificado multicanal con despacho inteligente entre Postiz, YouTube Nativo y Upload-Post."""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.publishing.native_youtube import NativeYouTubePublisher
from clipflow.publishing.postiz import PostizPublisher
from clipflow.publishing.upload_post import UploadPostPublisher

logger = logging.getLogger(__name__)


class UnifiedPublisher(BasePublisher):
    """
    Enrutador central de publicación que combina conectores nativos locales,
    Postiz autohospedado y Upload-Post de forma totalmente transparente y desacoplada.
    """

    def __init__(
        self,
        prefer_engine: Optional[str] = None,  # "postiz" | "native" | "upload_post" | "auto"
        mock_mode: bool = False,
    ):
        self.prefer_engine = prefer_engine or os.getenv("PUBLISHER_ENGINE", "auto")
        self.mock_mode = mock_mode

        self.native_youtube = NativeYouTubePublisher(mock_mode=mock_mode)
        self.postiz = PostizPublisher(mock_mode=mock_mode)
        self.upload_post = UploadPostPublisher(mock_mode=mock_mode)

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

        # Si se solicita explícitamente Postiz y está configurado
        if self.prefer_engine == "postiz" and self.postiz.is_configured:
            return self.postiz

        # Si es YouTube y tenemos OAuth nativo configurado
        if plat == "youtube" and self.native_youtube.is_configured:
            return self.native_youtube

        # Si Postiz está en línea y configurado
        if self.postiz.is_configured and self.postiz.verify_account("owner", plat):
            return self.postiz

        # Fallback a Upload-Post si está configurado
        if self.upload_post.is_configured:
            return self.upload_post

        # Por defecto según plataforma
        if plat == "youtube":
            return self.native_youtube
        if self.postiz.is_configured:
            return self.postiz
        return self.upload_post

    def list_profiles(self) -> List[Dict[str, Any]]:
        all_profiles: List[Dict[str, Any]] = []

        if self.native_youtube.is_configured:
            all_profiles.extend(self.native_youtube.list_profiles())

        if self.postiz.is_configured:
            all_profiles.extend(self.postiz.list_profiles())

        if self.upload_post.is_configured:
            all_profiles.extend(self.upload_post.list_profiles())

        # Si mock mode y lista vacía
        if self.mock_mode and not all_profiles:
            return [
                {
                    "username": "personal_owner",
                    "connected_platforms": ["youtube", "tiktok", "instagram"],
                    "reconnect_platforms": [],
                }
            ]

        # Deduplicar por nombre y plataforma
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
    ) -> PublishResult:
        pub = self.select_publisher_for_platform(platform)
        logger.info(
            "Despachando publicación para plataforma=%s usando motor=%s",
            platform,
            pub.__class__.__name__,
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

    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        if request_id.startswith("yt-native-"):
            return self.native_youtube.get_status(request_id, project_id)
        if request_id.startswith("postiz-"):
            return self.postiz.get_status(request_id, project_id)
        return self.upload_post.get_status(request_id, project_id)

    def cancel_scheduled(self, job_id: str) -> bool:
        if job_id.startswith("yt-native-"):
            return self.native_youtube.cancel_scheduled(job_id)
        if job_id.startswith("postiz-"):
            return self.postiz.cancel_scheduled(job_id)
        return self.upload_post.cancel_scheduled(job_id)
