"""Pruebas de programación horaria y conversión de zona horaria (GATE-6)."""
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.scheduler import SchedulerService, parse_schedule_to_utc


def test_timezone_conversion_america_santo_domingo():
    # En Santo Domingo (UTC-4 sin horario de verano), 09:00 hora local equivale a 13:00 UTC
    date_str = "2026-12-03 09:00:00"
    utc_dt = parse_schedule_to_utc(date_str, tz_name="America/Santo_Domingo")
    assert utc_dt.tzinfo == timezone.utc
    assert utc_dt.year == 2026
    assert utc_dt.month == 12
    assert utc_dt.day == 3
    assert utc_dt.hour == 13
    assert utc_dt.minute == 0


def test_scheduler_due_jobs_filtering(db_session: Session, vertical_video_path):
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id)
    db_session.add(project)
    db_session.flush()

    render = Render(project_id=project.id, target_platform="youtube_shorts", video_path=str(vertical_video_path))
    db_session.add(render)
    db_session.flush()

    draft = PostDraft(render_id=render.id, platform="youtube", title="Scheduled Post")
    db_session.add(draft)
    db_session.flush()

    now = utc_now()
    past_due_job = PublishJob(
        post_draft_id=draft.id,
        idempotency_key="job-past",
        scheduled_at_utc=now - timedelta(minutes=10),
        status="QUEUED",
    )
    future_job = PublishJob(
        post_draft_id=draft.id,
        idempotency_key="job-future",
        scheduled_at_utc=now + timedelta(hours=2),
        status="QUEUED",
    )
    db_session.add_all([past_due_job, future_job])
    db_session.commit()

    scheduler = SchedulerService(db_session)
    due_jobs = scheduler.get_due_jobs()
    due_ids = [j.id for j in due_jobs]

    assert past_due_job.id in due_ids
    assert future_job.id not in due_ids
