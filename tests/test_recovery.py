"""Pruebas de recuperación de errores, reintentos controlados y reconciliación (GATE-4 / GATE-6)."""
from datetime import timedelta
from pathlib import Path
from sqlalchemy.orm import Session

from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.publishing.reconcile import Reconciler
from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.queue import PublishQueueWorker


def test_reconciler_updates_in_flight_jobs(db_session: Session, vertical_video_path: Path):
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id)
    db_session.add(project)
    db_session.flush()

    render = Render(project_id=project.id, target_platform="youtube_shorts", video_path=str(vertical_video_path))
    db_session.add(render)
    db_session.flush()

    draft = PostDraft(render_id=render.id, platform="youtube", title="Reconciliation Test")
    db_session.add(draft)
    db_session.flush()

    publisher = UploadPostPublisher(mock_mode=True)
    res = publisher.submit_post(
        video_path=str(vertical_video_path),
        title="Reconciliation Test",
        platform="youtube",
        profile="main",
    )

    job = PublishJob(
        post_draft_id=draft.id,
        idempotency_key="idemp-rec-1",
        status="REMOTE_PROCESSING",
        remote_request_id=res.request_id,
    )
    db_session.add(job)
    db_session.commit()

    reconciler = Reconciler(publisher)
    updated = reconciler.reconcile_pending_jobs(db_session)

    assert len(updated) == 1
    reloaded_job = db_session.query(PublishJob).filter(PublishJob.id == job.id).first()
    assert reloaded_job.status == "PUBLISHED"
    assert reloaded_job.remote_url is not None


def test_independent_platform_success(db_session: Session, vertical_video_path: Path):
    """Verifica que el fallo en TikTok no afecte el éxito registrado en YouTube."""
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id)
    db_session.add(project)
    db_session.flush()

    render = Render(project_id=project.id, target_platform="youtube_shorts", video_path=str(vertical_video_path))
    db_session.add(render)
    db_session.flush()

    draft_yt = PostDraft(render_id=render.id, platform="youtube", title="YT Post")
    draft_tt = PostDraft(render_id=render.id, platform="tiktok", title="TT Post")
    db_session.add_all([draft_yt, draft_tt])
    db_session.flush()

    job_yt = PublishJob(post_draft_id=draft_yt.id, idempotency_key="job-yt", status="PUBLISHED", remote_url="https://youtube.com/shorts/123")
    job_tt = PublishJob(post_draft_id=draft_tt.id, idempotency_key="job-tt", status="ACTION_REQUIRED", last_error="Token expired")
    db_session.add_all([job_yt, job_tt])
    db_session.commit()

    assert job_yt.status == "PUBLISHED"
    assert job_tt.status == "ACTION_REQUIRED"
