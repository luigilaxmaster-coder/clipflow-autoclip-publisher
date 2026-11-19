"""Cola durable de trabajos, despachador de publicación y control de reintentos."""
from __future__ import annotations

import logging
import random
import time
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from clipflow.config import get_settings
from clipflow.db import record_audit
from clipflow.models import PostDraft, PublishJob, Render, utc_now
from clipflow.publishing.base import BasePublisher
from clipflow.publishing.reconcile import Reconciler

logger = logging.getLogger(__name__)


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
                PublishJob.status.in_(["QUEUED", "RETRYABLE_ERROR"]),
                (PublishJob.scheduled_at_utc == None) | (PublishJob.scheduled_at_utc <= now),
            )
            .order_by(PublishJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

        if not job:
            return None

        job.attempt_count += 1
        job.status = "UPLOADING"
        job.updated_at = now
        session.commit()

        draft: PostDraft = job.post_draft
        render: Render = draft.render

        logger.info(
            "Despachando trabajo %s (intento %d) para plataforma %s",
            job.id, job.attempt_count, draft.platform
        )

        try:
            res = self.publisher.submit_post(
                video_path=render.video_path,
                title=draft.title,
                platform=draft.platform,
                profile=draft.account_id or self.settings.publishing.profile,
                description=draft.description,
                visibility=draft.visibility,
                idempotency_key=job.idempotency_key,
                output_variant_id=render.output_variant_id,
                project_id=render.project.autoclip_project_id if render.project else None,
            )

            if res.ok:
                job.status = res.status
                job.remote_request_id = res.request_id
                job.remote_post_id = res.remote_post_id
                job.remote_url = res.remote_url
                job.last_error = None
                record_audit(session, "PublishJob", job.id, "worker", "submit_success", None, {"request_id": res.request_id})
            else:
                self._handle_failure(session, job, res.error or "Error desconocido al enviar publicación")

        except Exception as e:
            logger.exception("Error no capturado durante el envío del trabajo %s", job.id)
            self._handle_failure(session, job, str(e))

        session.commit()
        return job

    def _handle_failure(self, session: Session, job: PublishJob, error_msg: str):
        job.last_error = error_msg
        max_attempts = self.settings.publishing.retry_attempts

        # Clasificar si el error es recuperable (429, timeout, conexión temporal)
        is_transient = any(code in error_msg for code in ["429", "timeout", "500", "502", "503", "504", "reset"])
        
        if is_transient and job.attempt_count < max_attempts:
            job.status = "RETRYABLE_ERROR"
            # Backoff exponencial con jitter
            backoff_sec = (self.settings.publishing.retry_backoff_sec * (2 ** (job.attempt_count - 1))) + random.uniform(0.5, 2.0)
            job.scheduled_at_utc = utc_now() + timedelta(seconds=backoff_sec)
            record_audit(session, "PublishJob", job.id, "worker", "retry_scheduled", None, {"backoff_sec": backoff_sec, "attempt": job.attempt_count})
        else:
            job.status = "ACTION_REQUIRED"
            record_audit(session, "PublishJob", job.id, "worker", "action_required", None, {"error": error_msg, "attempt": job.attempt_count})

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
