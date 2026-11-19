"""Gestión de aprobación (manual y automática configurable) y políticas de publicación."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from clipflow.db import record_audit
from clipflow.models import PostDraft, PublishJob, Render, utc_now
from clipflow.config import get_settings

logger = logging.getLogger(__name__)


def generate_idempotency_key(render_id: str, platform: str, account_id: str, scheduled_at_iso: Optional[str] = None) -> str:
    """Genera una clave determinista única para evitar publicaciones duplicadas."""
    raw = f"{render_id}:{platform}:{account_id}:{scheduled_at_iso or 'immediate'}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
    return f"idemp-{platform}-{digest}"


class ApprovalService:
    def __init__(self, session: Session):
        self.session = session
        self.settings = get_settings()

    def approve_draft_manually(
        self,
        draft_id: str,
        actor: str,
        visibility: Optional[str] = None,
        scheduled_at_utc: Optional[datetime] = None,
    ) -> PublishJob:
        """Aprueba explícitamente un borrador y genera su trabajo en cola con idempotencia."""
        draft = self.session.query(PostDraft).filter(PostDraft.id == draft_id).first()
        if not draft:
            raise ValueError(f"Borrador no encontrado: {draft_id}")

        old_vis = draft.visibility
        if visibility:
            draft.visibility = visibility

        draft.approved_at = utc_now()
        draft.approval_actor = actor

        # Crear trabajo de publicación con clave de idempotencia
        idemp_key = generate_idempotency_key(
            render_id=draft.render_id,
            platform=draft.platform,
            account_id=draft.account_id or "default",
            scheduled_at_iso=scheduled_at_utc.isoformat() if scheduled_at_utc else None,
        )

        existing_job = self.session.query(PublishJob).filter(PublishJob.idempotency_key == idemp_key).first()
        if existing_job:
            return existing_job

        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key=idemp_key,
            scheduled_at_utc=scheduled_at_utc,
            status="QUEUED",
            attempt_count=0,
        )
        self.session.add(job)
        self.session.flush()

        record_audit(
            self.session,
            "PostDraft",
            draft.id,
            actor,
            "manual_approval",
            {"visibility": old_vis},
            {"visibility": draft.visibility, "job_id": job.id, "idempotency_key": idemp_key},
        )
        self.session.commit()
        return job

    def check_auto_approval_policy(self, render: Render, draft: PostDraft) -> Tuple[bool, str]:
        """Evalúa si un borrador cumple los criterios para aprobación automática."""
        cfg = self.settings.publishing
        if cfg.mode != "auto_approved":
            return False, f"El modo de publicación actual es '{cfg.mode}', no 'auto_approved'."

        # Verificar QA
        if cfg.require_qa_pass and render.qa_status != "PASSED":
            return False, f"El render no tiene estatus QA PASSED (actual: {render.qa_status})."

        # Verificar puntuación del candidato si existe
        if render.candidate and render.candidate.score < self.settings.analysis.min_quality_score:
            return False, f"La puntuación {render.candidate.score:.2f} es menor a {self.settings.analysis.min_quality_score}."

        # Verificar cuota diaria por plataforma
        today_start = utc_now().replace(hour=0, minute=0, second=0, microsecond=0)
        published_today = (
            self.session.query(PublishJob)
            .join(PostDraft)
            .filter(
                PostDraft.platform == draft.platform,
                PublishJob.status == "PUBLISHED",
                PublishJob.updated_at >= today_start,
            )
            .count()
        )
        if published_today >= cfg.max_publishes_per_day_per_platform:
            return False, f"Límite diario alcanzado para {draft.platform} ({published_today}/{cfg.max_publishes_per_day_per_platform})."

        return True, "Políticas de aprobación automática satisfechas."
