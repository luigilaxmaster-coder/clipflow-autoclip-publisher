"""Prueba de integración End-to-End del pipeline completo de clipflow."""
import json
from pathlib import Path
from sqlalchemy.orm import Session

from clipflow.approvals import ApprovalService
from clipflow.ingestion import IngestionService
from clipflow.media_qa import MediaQA
from clipflow.metadata import MetadataGenerator
from clipflow.models import PostDraft, Project, PublishJob, Render
from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.queue import PublishQueueWorker
from clipflow.reporting import ReportingService


def test_end_to_end_orchestration_flow(db_session: Session, vertical_video_path: Path):
    qa = MediaQA()
    ingestion = IngestionService(qa)
    meta_gen = MetadataGenerator()
    publisher = UploadPostPublisher(mock_mode=True)
    worker = PublishQueueWorker(lambda: db_session, publisher)
    reporting = ReportingService(db_session)

    # 1. Ingesta
    source = ingestion.process_source(str(vertical_video_path))
    db_session.add(source)
    db_session.flush()
    assert source.id is not None
    assert source.sha256 is not None

    # 2. Creación del proyecto
    project = Project(source_id=source.id, workflow_status="INGESTED")
    db_session.add(project)
    db_session.flush()

    # 3. QA del video de salida
    qa_report = qa.validate_clip(vertical_video_path, platform="youtube_shorts", min_duration_sec=1.0)
    assert qa_report.passed is True

    # 4. Registro del Render 9:16
    render = Render(
        project_id=project.id,
        output_variant_id="variant-e2e-1",
        studio_job_id="job-e2e-1",
        target_platform="youtube_shorts",
        video_path=str(vertical_video_path),
        duration_sec=qa_report.duration_sec,
        width=qa_report.width,
        height=qa_report.height,
        qa_status=qa_report.overall_status,
        qa_json=json.dumps(qa_report.to_dict()),
    )
    db_session.add(render)
    db_session.flush()

    # 5. Generación de borradores
    drafts = meta_gen.generate_drafts(
        render_id=render.id,
        base_title="E2E Video Test Title",
        tags=["automation", "autoclip"],
        visibility_yt="private",
        visibility_tt="SELF_ONLY",
    )
    for d in drafts:
        db_session.add(d)
    db_session.flush()
    assert len(drafts) == 2

    # 6. Aprobación y puesta en cola
    approval_svc = ApprovalService(db_session)
    yt_draft = next(d for d in drafts if d.platform == "youtube")
    tt_draft = next(d for d in drafts if d.platform == "tiktok")

    job_yt = approval_svc.approve_draft_manually(yt_draft.id, actor="e2e_tester")
    job_tt = approval_svc.approve_draft_manually(tt_draft.id, actor="e2e_tester")
    assert job_yt.status == "QUEUED"
    assert job_tt.status == "QUEUED"

    # 7. Ejecución del worker
    worker.process_due_job(db_session)
    worker.process_due_job(db_session)

    # 8. Reconciliación
    worker.reconciler.reconcile_pending_jobs(db_session)

    # 9. Verificación de publicación exitosa
    db_session.refresh(job_yt)
    db_session.refresh(job_tt)
    assert job_yt.status == "PUBLISHED"
    assert job_tt.status == "PUBLISHED"
    assert job_yt.remote_url is not None
    assert job_tt.remote_url is not None

    # 10. Resumen y reporte
    summary = reporting.get_project_summary(project.id)
    assert summary["project_id"] == project.id
    assert summary["renders_count"] == 1
    assert len(summary["renders"][0]["drafts"]) == 2
    assert summary["renders"][0]["drafts"][0]["jobs"][0]["status"] == "PUBLISHED"
