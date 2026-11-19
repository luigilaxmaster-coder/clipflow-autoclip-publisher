"""Interfaz base abstracta para adaptadores de publicación en redes sociales."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class PublishResult:
    ok: bool
    request_id: str
    job_id: Optional[str] = None
    status: str = "QUEUED"  # QUEUED | UPLOADING | REMOTE_PROCESSING | PUBLISHED | FAILED
    remote_post_id: Optional[str] = None
    remote_url: Optional[str] = None
    error: Optional[str] = None
    platform_results: List[Dict[str, Any]] = field(default_factory=list)
    raw_payload: Dict[str, Any] = field(default_factory=dict)


class BasePublisher(ABC):
    @abstractmethod
    def list_profiles(self) -> List[Dict[str, Any]]:
        """Lista los perfiles y plataformas conectadas disponibles."""
        pass

    @abstractmethod
    def verify_account(self, profile: str, platform: str) -> bool:
        """Verifica que el perfil especificado tenga la plataforma conectada y autorizada."""
        pass

    @abstractmethod
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
        """Envía el video final al proveedor de publicación."""
        pass

    @abstractmethod
    def get_status(self, request_id: str, project_id: Optional[str] = None) -> PublishResult:
        """Consulta el estado remoto de la publicación asíncrona."""
        pass

    @abstractmethod
    def cancel_scheduled(self, job_id: str) -> bool:
        """Cancela una publicación programada si aún no ha sido emitida."""
        pass
