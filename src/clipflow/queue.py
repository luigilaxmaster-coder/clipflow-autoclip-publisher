"""Cola durable de trabajos, despachador de publicación y control de reintentos con máquina de estados completa."""
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from clipflow.config import get_settings
from clipflow.db import record_audit
from clipflow.models import (
    AccountConnection,
    ConnectionStatus,
    JobStatus,
    PostDraft,
    PublishJob,
    Render,
    utc_now,
)
from clipflow.publishing.base import BasePublisher
from clipflow.publishing.reconcile import Reconciler

logger = logging.getLogger(__name__)

AUTH_ERROR_PATTERNS = [
    "401",
    "unauthorized",
    "token_expired",
    "invalid_grant",
    "permission_denied",
    "oauth_required",
    "invalid api key",
]

TRANSIENT_ERROR_PATTERNS = [
    "429",
    "timeout",
    "500",
    "502",
    "503",
    "504",
    "reset",
    "connection refused",
    "network unreachable",
]


class PublishQueueWorker:
    def __init__(self, session_factory, publisher: BasePublisher):
        self.session_factory = session_factory
        self.publisher = publisher
        self.reconciler = Reconciler(publisher)
        self.settings = get_settings()
        self._running = False

    def process_due_job(self, session: Session) -> Optional[PublishJob]:
        """Reclama y procesa atómicamente el siguiente trabajo listo en la cola."""
        now = utc_now()
        # Buscar un trabajo listo (inmediato o con fecha programada ya cumplida)
        job = (
            session.query(PublishJob)
            .filter(
                PublishJob.status.in_([JobStatus.QUEUED, JobStatus.SCHEDULED, "RETRYABLE_ERROR"]),
                (PublishJob.scheduled_at_utc == None) | (PublishJob.scheduled_at_utc <= now),
            )
            .order_by(PublishJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

        if not job:
            return None

        draft: PostDraft = job.post_draft
        render: Optional[Render] = draft.render if draft else None

        # Pre-reconciliación: Si ya tiene un remote_request_id previo, consultar antes de re-subir
        if job.remote_request_id:
            try:
                pre_check = self.publisher.get_status(job.remote_request_id)
                if pre_check.ok and pre_check.status in (JobStatus.PUBLISHED, "PUBLISHED"):
                    job.status = JobStatus.PUBLISHED
                    job.remote_url = pre_check.remote_url or job.remote_url
                    job.updated_at = now
                    record_audit(session, "PublishJob", job.id, "worker", "pre_reconciliation_published", None, {"remote_url": job.remote_url})
                    session.commit()
                    return job
            except Exception as e:
                logger.debug("Pre-reconciliación no concluyente para job %s: %s", job.id, e)

        # Verificar si la plataforma está pausada por fallo de autenticación previo
        platform_name = draft.platform.lower() if draft else "unknown"
        conn = session.query(AccountConnection).filter(AccountConnection.provider_id == platform_name).first()
        if conn and conn.status in (ConnectionStatus.EXPIRED, ConnectionStatus.INSUFFICIENT_PERMS):
            job.status = JobStatus.REQUIRES_ACTION
            job.last_error = f"Publicación pausada automáticamente: La conexión con '{platform_name}' requiere re-autorización."
            job.updated_at = now
            record_audit(session, "PublishJob", job.id, "worker", "auth_pause", None, {"platform": platform_name})
            session.commit()
            return job

        job.attempt_count += 1
        job.status = JobStatus.UPLOADING
        job.updated_at = now
        session.commit()

        video_path = render.video_path if render else ""
        logger.info(
            "Despachando trabajo %s (intento %d) para plataforma %s [idempotency=%s]",
            job.id,
            job.attempt_count,
            draft.platform if draft else "unknown",
            job.idempotency_key[:8] + "...",
        )

        try:
            res = self.publisher.submit_post(
                video_path=video_path,
                title=draft.title if draft else "Untitled",
                platform=draft.platform if draft else "unknown",
                profile=draft.account_id or self.settings.publishing.profile,
                description=draft.description if draft else None,
                visibility=draft.visibility if draft else "private",
                idempotency_key=job.idempotency_key,
                output_variant_id=render.output_variant_id if render else None,
                project_id=render.project.autoclip_project_id if (render and render.project) else None,
            )

            if res.ok:
                job.status = res.status
                job.remote_request_id = res.request_id
                job.remote_post_id = res.remote_post_id
                job.remote_url = res.remote_url
                job.last_error = None
                record_audit(session, "PublishJob", job.id, "worker", "submit_success", None, {"request_id": res.request_id})
            else:
                self._handle_failure(session, job, res.error or "Error desconocido al enviar publicación", platform_name)

        except Exception as e:
            logger.exception("Error no capturado durante el envío del trabajo %s", job.id)
            self._handle_failure(session, job, str(e), platform_name)

        session.commit()
        return job

    def _handle_failure(self, session: Session, job: PublishJob, error_msg: str, platform: str):
        job.last_error = error_msg
        err_lower = error_msg.lower()

        # 1. Error de autorización / token expirado -> Pausa inmediata
        if any(pat in err_lower for pat in AUTH_ERROR_PATTERNS):
            job.status = JobStatus.REQUIRES_ACTION
            conn = session.query(AccountConnection).filter(AccountConnection.provider_id == platform).first()
            if conn:
                conn.status = ConnectionStatus.EXPIRED
                conn.last_error = error_msg
            record_audit(session, "PublishJob", job.id, "worker", "auth_failure_pause", None, {"error": error_msg, "platform": platform})
            return

        # 2. Error transitorio (429, timeout, red) -> Reintento con backoff y jitter
        max_attempts = self.settings.publishing.retry_attempts
        is_transient = any(code in err_lower for code in TRANSIENT_ERROR_PATTERNS)

        if is_transient and job.attempt_count < max_attempts:
            job.status = "RETRYABLE_ERROR"
            backoff_sec = (self.settings.publishing.retry_backoff_sec * (2 ** (job.attempt_count - 1))) + random.uniform(0.5, 2.0)
            job.scheduled_at_utc = utc_now() + timedelta(seconds=backoff_sec)
            record_audit(session, "PublishJob", job.id, "worker", "retry_scheduled", None, {"backoff_sec": backoff_sec, "attempt": job.attempt_count})
        else:
            job.status = JobStatus.FAILED
            record_audit(session, "PublishJob", job.id, "worker", "job_failed", None, {"error": error_msg, "attempt": job.attempt_count})

    def run_loop(self, poll_interval_sec: float = 5.0, once: bool = False):
        """Ejecuta el ciclo de procesamiento y reconciliación continua."""
        self._running = True
        logger.info("Iniciando ciclo del worker de clipflow (intervalo: %ds)", poll_interval_sec)

        try:
            while self._running:
                with self.session_factory() as session:
                    # 1. Reconciliar tareas remotas pendientes
                    self.reconciler.reconcile_pending_jobs(session)

                    # 2. Despachar un trabajo listo
                    job = self.process_due_job(session)

                if once:
                    break

                if not job:
                    time.sleep(poll_interval_sec)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Deteniendo worker de forma segura...")
        finally:
            self._running = False

    def stop(self):
        self._running = False
