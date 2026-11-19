"""Reconciliación y sincronización de estados remotos de publicación."""
from __future__ import annotations

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from clipflow.db import record_audit
from clipflow.models import PublishJob, utc_now
from clipflow.publishing.base import BasePublisher

logger = logging.getLogger(__name__)


class Reconciler:
    def __init__(self, publisher: BasePublisher):
        self.publisher = publisher

    def reconcile_pending_jobs(self, session: Session) -> List[PublishJob]:
        """Consulta todos los trabajos en procesamiento remoto y actualiza su estado."""
        in_flight_statuses = ["REMOTE_PROCESSING", "UPLOADING"]
        jobs = session.query(PublishJob).filter(PublishJob.status.in_(in_flight_statuses)).all()
        updated_jobs: List[PublishJob] = []

        for job in jobs:
            if not job.remote_request_id:
                continue

            try:
                res = self.publisher.get_status(job.remote_request_id)
                old_status = job.status
                job.last_checked_at = utc_now()

                if res.status == "PUBLISHED":
                    job.status = "PUBLISHED"
                    job.remote_url = res.remote_url
                    job.remote_post_id = res.remote_post_id
                    job.last_error = None
                    record_audit(session, "PublishJob", job.id, "reconciler", "publish_succeeded", {"status": old_status}, {"status": "PUBLISHED", "url": res.remote_url})
                    updated_jobs.append(job)

                elif res.status in ("FAILED", "ACTION_REQUIRED", "REMOTE_REJECTED"):
                    job.status = res.status
                    job.last_error = res.error or "Error remoto o acción manual requerida"
                    record_audit(session, "PublishJob", job.id, "reconciler", "publish_failed", {"status": old_status}, {"status": job.status, "error": job.last_error})
                    updated_jobs.append(job)

                elif res.status == "REMOTE_PROCESSING":
                    # Aún en progreso
                    pass

            except Exception as e:
                logger.warning("Fallo al reconciliar trabajo %s: %s", job.id, e)

        session.commit()
        return updated_jobs
