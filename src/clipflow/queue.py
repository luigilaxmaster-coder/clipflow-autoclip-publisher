"""Cola durable de trabajos, despachador de publicación y control de reintentos anti-duplicados."""
from __future__ import annotations

import inspect
import logging
import random
import time
from datetime import timedelta
from typing import Optional

from sqlalchemy.orm import Session

from clipflow.config import get_settings
from clipflow.connections.service import ConnectionService
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
from clipflow.publishing.unified import UnifiedPublisher
from clipflow.security import redact_secrets

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
    "interrupción de red",
]


class PublishQueueWorker:
    def __init__(self, session_factory, publisher: Optional[BasePublisher] = None):
        self.session_factory = session_factory
        self.publisher = publisher
        self.reconciler = Reconciler(publisher) if publisher is not None else None
        self.settings = get_settings()
        self._running = False

    def _get_publisher(self, session: Session) -> BasePublisher:
        if self.publisher is not None:
            if isinstance(self.publisher, UnifiedPublisher) and not self.publisher.mock_mode:
                self.publisher.connection_service = ConnectionService(session)
                self.publisher.refresh_connections()
            return self.publisher
        return UnifiedPublisher(connection_service=ConnectionService(session), mock_mode=False)

    def _query_remote_status(self, pub: BasePublisher, lookup_id: str, platform: str, account_ref: Optional[str] = None):
        sig = inspect.signature(pub.get_status)
        kwargs = {}
        if "platform" in sig.parameters:
            kwargs["platform"] = platform
        if "account_ref" in sig.parameters and account_ref:
            kwargs["account_ref"] = account_ref
        return pub.get_status(lookup_id, **kwargs)  # type: ignore[call-arg]

    def process_due_job(self, session: Session) -> Optional[PublishJob]:
        """Reclama y procesa atómicamente el siguiente trabajo listo en la cola."""
        now = utc_now()
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

        pub = self._get_publisher(session)
        draft: PostDraft = job.post_draft
        render: Optional[Render] = draft.render if draft else None
        platform_name = draft.platform.lower() if draft else "unknown"

        account_ref = draft.account_id if draft else None

        # 1. Pre-reconciliación estricta anti-duplicados:
        # Si ya existe remote_post_id o remote_request_id previo, verificar antes de re-subir
        lookup_id = job.remote_post_id or job.remote_request_id
        if lookup_id:
            try:
                pre_check = self._query_remote_status(pub, lookup_id, platform_name, account_ref=account_ref)
                if pre_check.ok and pre_check.status in (JobStatus.PUBLISHED, "PUBLISHED"):
                    job.status = JobStatus.PUBLISHED
                    job.remote_post_id = pre_check.remote_post_id or job.remote_post_id
                    job.remote_url = pre_check.remote_url or job.remote_url
                    job.last_error = None
                    job.updated_at = now
                    record_audit(
                        session,
                        "PublishJob",
                        job.id,
                        "worker",
                        "pre_reconciliation_published",
                        None,
                        {"remote_post_id": job.remote_post_id, "remote_url": job.remote_url},
                    )
                    session.commit()
                    return job
                if pre_check.ok and pre_check.status in (JobStatus.PROCESSING, "REMOTE_PROCESSING"):
                    job.status = pre_check.status
                    job.remote_post_id = pre_check.remote_post_id or job.remote_post_id
                    job.updated_at = now
                    session.commit()
                    return job
            except Exception as e:
                logger.debug("Pre-reconciliación no concluyente para job %s: %s", job.id, redact_secrets(str(e)))

        # 2. Verificar estado del perfil específico y su límite diario configurado
        conn_svc = ConnectionService(session)
        conn = conn_svc.get_connection(platform_name, account_ref=account_ref)
        if conn and conn.status in (ConnectionStatus.EXPIRED, ConnectionStatus.INSUFFICIENT_PERMS):
            job.status = JobStatus.REQUIRES_ACTION
            job.last_error = (
                f"Publicación pausada automáticamente: El perfil '{conn.account_name}' ({platform_name}) "
                "requiere re-autorización."
            )
            job.updated_at = now
            record_audit(session, "PublishJob", job.id, "worker", "auth_pause", None, {"platform": platform_name, "connection_id": conn.id})
            session.commit()
            return job

        if conn and not getattr(pub, "mock_mode", False):
            limit = max(1, int(conn.daily_upload_limit or 6))
            used_today = conn_svc.count_uploads_last_24h(conn)
            if used_today >= limit:
                job.status = "QUEUED_FOR_DAILY_QUOTA"
                job.last_error = (
                    f"Cupo diario alcanzado para '{conn.account_name}' ({used_today}/{limit} en 24h). "
                    "Esperando ventana disponible sin redirigir a otros canales."
                )
                job.updated_at = now
                record_audit(
                    session,
                    "PublishJob",
                    job.id,
                    "worker",
                    "daily_quota_reached",
                    None,
                    {"connection_id": conn.id, "used_today": used_today, "limit": limit},
                )
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
            platform_name,
            job.idempotency_key[:8] + "...",
        )

        def _save_session_uri(uri: str) -> None:
            job.upload_session_uri = uri
            job.updated_at = utc_now()
            session.commit()

        def _save_progress(bytes_sent: int) -> None:
            job.upload_bytes_sent = bytes_sent
            job.updated_at = utc_now()
            session.commit()

        try:
            submit_kwargs = dict(
                video_path=video_path,
                title=draft.title if draft else "Untitled",
                platform=draft.platform if draft else "unknown",
                profile=(draft.account_id if draft else None) or self.settings.publishing.profile,
                description=draft.description if draft else None,
                visibility=draft.visibility if draft else "private",
                idempotency_key=job.idempotency_key,
                output_variant_id=render.output_variant_id if render else None,
                project_id=render.project.autoclip_project_id if (render and render.project) else None,
            )
            submit_sig = inspect.signature(pub.submit_post)
            if "existing_session_uri" in submit_sig.parameters:
                submit_kwargs["existing_session_uri"] = job.upload_session_uri
                submit_kwargs["on_session_created"] = _save_session_uri
                submit_kwargs["on_progress"] = _save_progress

            res = pub.submit_post(**submit_kwargs)

            if res.ok:
                job.status = res.status
                job.remote_request_id = res.request_id
                job.remote_post_id = res.remote_post_id or job.remote_post_id
                job.remote_url = res.remote_url or job.remote_url
                if isinstance(res.raw_payload, dict):
                    if res.raw_payload.get("channelId"):
                        job.remote_channel_id = str(res.raw_payload["channelId"])
                    if res.raw_payload.get("privacyStatus"):
                        job.remote_privacy_status = str(res.raw_payload["privacyStatus"])
                # Si ya tenemos remote_post_id confirmado, limpiar la URI temporal de sesión
                if job.remote_post_id and job.status in (JobStatus.PUBLISHED, JobStatus.PROCESSING, JobStatus.SCHEDULED):
                    job.upload_session_uri = None
                job.last_error = None
                record_audit(
                    session,
                    "PublishJob",
                    job.id,
                    "worker",
                    "submit_success",
                    None,
                    {
                        "request_id": res.request_id,
                        "remote_post_id": job.remote_post_id,
                        "status": job.status,
                    },
                )
            else:
                self._handle_failure(
                    session,
                    job,
                    redact_secrets(res.error or "Error desconocido al enviar publicación"),
                    platform_name,
                )

        except Exception as e:
            safe_err = redact_secrets(str(e))
            logger.error("Error no capturado durante el envío del trabajo %s: %s", job.id, safe_err)
            self._handle_failure(session, job, safe_err, platform_name)

        session.commit()
        return job

    def _handle_failure(self, session: Session, job: PublishJob, error_msg: str, platform: str):
        safe_error = redact_secrets(error_msg)
        job.last_error = safe_error
        err_lower = safe_error.lower()

        if any(pat in err_lower for pat in AUTH_ERROR_PATTERNS):
            job.status = JobStatus.REQUIRES_ACTION
            account_ref = job.post_draft.account_id if job.post_draft else None
            conn = ConnectionService(session).get_connection(platform, account_ref=account_ref)
            if conn:
                conn.status = ConnectionStatus.EXPIRED
                conn.last_error = safe_error
            record_audit(
                session,
                "PublishJob",
                job.id,
                "worker",
                "auth_failure_pause",
                None,
                {"error": safe_error, "platform": platform},
            )
            return

        max_attempts = self.settings.publishing.retry_attempts
        is_transient = any(code in err_lower for code in TRANSIENT_ERROR_PATTERNS)

        if is_transient and job.attempt_count < max_attempts:
            job.status = "RETRYABLE_ERROR"
            backoff_sec = (
                self.settings.publishing.retry_backoff_sec * (2 ** (job.attempt_count - 1))
            ) + random.uniform(0.5, 2.0)
            job.scheduled_at_utc = utc_now() + timedelta(seconds=backoff_sec)
            record_audit(
                session,
                "PublishJob",
                job.id,
                "worker",
                "retry_scheduled",
                None,
                {"backoff_sec": round(backoff_sec, 2), "attempt": job.attempt_count},
            )
        else:
            job.status = JobStatus.FAILED
            record_audit(
                session,
                "PublishJob",
                job.id,
                "worker",
                "job_failed",
                None,
                {"error": safe_error, "attempt": job.attempt_count},
            )

    def run_loop(self, poll_interval_sec: float = 5.0, once: bool = False):
        """Ejecuta el ciclo de procesamiento y reconciliación continua."""
        self._running = True
        logger.info("Iniciando ciclo del worker de clipflow (intervalo: %ss)", poll_interval_sec)

        try:
            while self._running:
                with self.session_factory() as session:
                    pub = self._get_publisher(session)
                    reconciler = self.reconciler or Reconciler(pub)
                    reconciler.publisher = pub
                    reconciler.reconcile_pending_jobs(session)
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
