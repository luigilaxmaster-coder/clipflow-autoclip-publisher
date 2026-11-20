"""Reconciliación y sincronización de estados remotos de publicación usando identificadores reales."""
from __future__ import annotations

import inspect
import logging
from pathlib import Path
from typing import List

from sqlalchemy.orm import Session

from clipflow.db import record_audit
from clipflow.models import PublishJob, utc_now
from clipflow.publishing.base import BasePublisher
from clipflow.security import redact_secrets

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(self, publisher: BasePublisher):
        self.publisher = publisher

    def _query_status(self, lookup_id: str, platform: str):
        sig = inspect.signature(self.publisher.get_status)
        if "platform" in sig.parameters:
            return self.publisher.get_status(lookup_id, platform=platform)  # type: ignore[call-arg]
        return self.publisher.get_status(lookup_id)

    def reconcile_pending_jobs(self, session: Session) -> List[PublishJob]:
        """Consulta todos los trabajos en procesamiento remoto o subida interrumpida y actualiza su estado."""
        in_flight_statuses = ["REMOTE_PROCESSING", "PROCESSING", "UPLOADING"]
        jobs = session.query(PublishJob).filter(PublishJob.status.in_(in_flight_statuses)).all()
        updated_jobs: List[PublishJob] = []

        for job in jobs:
            platform = job.post_draft.platform.lower() if job.post_draft else ""
            lookup_id = job.remote_post_id or job.remote_request_id

            # Caso 1: Subida resumible interrumpida (hay upload_session_uri pero aún no hay remote_post_id)
            if (
                not job.remote_post_id
                and job.upload_session_uri
                and platform == "youtube"
                and hasattr(self.publisher, "native_youtube")
            ):
                render = job.post_draft.render if job.post_draft else None
                video_path = render.video_path if render else None
                if video_path and Path(video_path).is_file():
                    try:
                        total_size = Path(video_path).stat().st_size
                        probe = self.publisher.native_youtube.probe_resumable_upload(
                            job.upload_session_uri, total_size
                        )
                        job.last_checked_at = utc_now()
                        if probe.get("state") == "completed" and probe.get("video_id"):
                            vid = probe["video_id"]
                            job.remote_post_id = vid
                            job.remote_url = f"https://www.youtube.com/shorts/{vid}"
                            job.status = "PUBLISHED"
                            job.last_error = None
                            record_audit(
                                session,
                                "PublishJob",
                                job.id,
                                "reconciler",
                                "resumable_session_recovered",
                                None,
                                {"video_id": vid, "url": job.remote_url},
                            )
                            updated_jobs.append(job)
                            continue
                    except Exception as e:
                        logger.warning(
                            "Fallo al sondear sesión resumible de %s: %s",
                            job.id,
                            redact_secrets(str(e)),
                        )

            if not lookup_id:
                continue

            try:
                res = self._query_status(lookup_id, platform)
                old_status = job.status
                job.last_checked_at = utc_now()

                if res.status == "PUBLISHED":
                    job.status = "PUBLISHED"
                    job.remote_url = res.remote_url or job.remote_url
                    job.remote_post_id = res.remote_post_id or job.remote_post_id
                    job.last_error = None
                    record_audit(
                        session,
                        "PublishJob",
                        job.id,
                        "reconciler",
                        "publish_succeeded",
                        {"status": old_status},
                        {"status": "PUBLISHED", "remote_post_id": job.remote_post_id, "url": job.remote_url},
                    )
                    updated_jobs.append(job)

                elif res.status in ("FAILED", "ACTION_REQUIRED", "REQUIRES_ACTION", "REMOTE_REJECTED"):
                    job.status = res.status
                    job.last_error = redact_secrets(res.error or "Error remoto o acción manual requerida")
                    record_audit(
                        session,
                        "PublishJob",
                        job.id,
                        "reconciler",
                        "publish_failed",
                        {"status": old_status},
                        {"status": job.status, "error": job.last_error},
                    )
                    updated_jobs.append(job)

                elif res.status in ("REMOTE_PROCESSING", "PROCESSING"):
                    if res.remote_post_id and not job.remote_post_id:
                        job.remote_post_id = res.remote_post_id
                        updated_jobs.append(job)

            except Exception as e:
                logger.warning("Fallo al reconciliar trabajo %s: %s", job.id, redact_secrets(str(e)))

        session.commit()
        return updated_jobs
