"""Pruebas de prevención de publicaciones duplicadas e idempotencia (RF-06)."""
from pathlib import Path
from sqlalchemy.orm import Session

from clipflow.approvals import ApprovalService, generate_idempotency_key
from clipflow.models import PostDraft, Project, PublishJob, Render, Source


def test_deterministic_idempotency_key():
    k1 = generate_idempotency_key("render-1", "youtube", "main", "2026-12-01T09:00:00")
    k2 = generate_idempotency_key("render-1", "youtube", "main", "2026-12-01T09:00:00")
    k3 = generate_idempotency_key("render-1", "tiktok", "main", "2026-12-01T09:00:00")
    assert k1 == k2
    assert k1 != k3


def test_approval_service_prevents_duplicate_jobs(db_session: Session, vertical_video_path: Path):
    # Setup test entities
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id, workflow_status="READY_TO_REVIEW")
    db_session.add(project)
    db_session.flush()

    render = Render(project_id=project.id, target_platform="youtube_shorts", video_path=str(vertical_video_path))
    db_session.add(render)
    db_session.flush()

    draft = PostDraft(render_id=render.id, platform="youtube", title="Test Idempotency")
    db_session.add(draft)
    db_session.commit()

    approval_svc = ApprovalService(db_session)
    job1 = approval_svc.approve_draft_manually(draft.id, actor="tester")
    job2 = approval_svc.approve_draft_manually(draft.id, actor="tester")

    assert job1.id == job2.id
    assert job1.idempotency_key == job2.idempotency_key

    # Total jobs in db must be exactly 1
    total = db_session.query(PublishJob).filter(PublishJob.post_draft_id == draft.id).count()
    assert total == 1
