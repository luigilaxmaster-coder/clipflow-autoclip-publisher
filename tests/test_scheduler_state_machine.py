"""Pruebas de la máquina de estados completa de 10 estados y pausa automática por autenticación."""
import pytest
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy.orm import Session

from clipflow.models import (
    AccountConnection,
    ConnectionStatus,
    JobStatus,
    PostDraft,
    Project,
    PublishJob,
    Render,
    Source,
)
from clipflow.publishing.base import BasePublisher, PublishResult
from clipflow.queue import PublishQueueWorker


class MockFailingPublisher(BasePublisher):
    def __init__(self, fail_with_auth: bool = False):
        self.fail_with_auth = fail_with_auth

    def list_profiles(self):
        return []

    def verify_account(self, profile, platform):
        return True

    def submit_post(self, **kwargs):
        if self.fail_with_auth:
            return PublishResult(ok=False, request_id="fail-1", status="FAILED", error="HTTP 401 Unauthorized: token_expired")
        return PublishResult(ok=False, request_id="fail-2", status="FAILED", error="HTTP 429 Too Many Requests")

    def get_status(self, request_id, project_id=None):
        return PublishResult(ok=False, request_id=request_id, status="FAILED")

    def cancel_scheduled(self, job_id):
        return True


def test_auth_failure_pauses_queue(db_session: Session, vertical_video_path: Path):
    # 1. Configurar draft y job
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id, workflow_status="COMPLETED")
    db_session.add(project)
    db_session.flush()

    render = Render(project_id=project.id, target_platform="youtube", video_path=str(vertical_video_path))
    db_session.add(render)
    db_session.flush()

    draft = PostDraft(render_id=render.id, platform="youtube", title="Test Auth Pause")
    db_session.add(draft)
    db_session.flush()

    job = PublishJob(post_draft_id=draft.id, idempotency_key="idemp-auth-1", status=JobStatus.QUEUED)
    db_session.add(job)

    # Conexión activa en BD
    conn = AccountConnection(provider_id="youtube", account_name="@MyChannel", status=ConnectionStatus.CONNECTED)
    db_session.add(conn)
    db_session.commit()

    # 2. Worker con fallo de autenticación 401
    failing_pub = MockFailingPublisher(fail_with_auth=True)
    worker = PublishQueueWorker(lambda: db_session, failing_pub)

    processed = worker.process_due_job(db_session)
    assert processed is not None
    assert processed.status == JobStatus.REQUIRES_ACTION

    # 3. La conexión debe quedar marcada automáticamente como EXPIRED
    db_session.refresh(conn)
    assert conn.status == ConnectionStatus.EXPIRED

    # 4. Un segundo trabajo para la misma plataforma debe pausarse preventivamente
    draft2 = PostDraft(render_id=render.id, platform="youtube", title="Test Auth Pause 2")
    db_session.add(draft2)
    db_session.flush()
    job2 = PublishJob(post_draft_id=draft2.id, idempotency_key="idemp-auth-2", status=JobStatus.QUEUED)
    db_session.add(job2)
    db_session.commit()

    processed2 = worker.process_due_job(db_session)
    assert processed2 is not None
    assert processed2.status == JobStatus.REQUIRES_ACTION
    assert "re-autorización" in processed2.last_error
