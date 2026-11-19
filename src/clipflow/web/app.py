"""Aplicación FastAPI para el panel de administración web de ClipFlow."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from clipflow.approvals import ApprovalService
from clipflow.config import get_settings
from clipflow.db import get_db, init_db
from clipflow.models import AuditEvent, PostDraft, Project, PublishJob, Render
from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.queue import PublishQueueWorker

app = FastAPI(title="ClipFlow Admin Panel", version="0.1.0")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@app.on_event("startup")
def startup_event():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    renders_count = db.query(Render).count()
    published_count = db.query(PublishJob).filter(PublishJob.status == "PUBLISHED").count()
    queued_count = db.query(PublishJob).filter(PublishJob.status == "QUEUED").count()
    failed_count = db.query(PublishJob).filter(PublishJob.status.in_(["FAILED", "ACTION_REQUIRED"])).count()

    stats = {
        "projects_count": len(projects),
        "renders_count": renders_count,
        "published_count": published_count,
        "queued_count": queued_count,
        "failed_count": failed_count,
    }
    mode = get_settings().publishing.mode

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"projects": projects, "stats": stats, "mode": mode},
    )


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: str, request: Request, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    return templates.TemplateResponse(
        request=request,
        name="project_detail.html",
        context={"project": project},
    )


@app.get("/queue", response_class=HTMLResponse)
def queue_view(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(PublishJob).order_by(PublishJob.created_at.desc()).all()
    return templates.TemplateResponse(
        request=request,
        name="queue.html",
        context={"jobs": jobs},
    )


@app.get("/audit", response_class=HTMLResponse)
def audit_view(request: Request, db: Session = Depends(get_db)):
    events = db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100).all()
    return templates.TemplateResponse(
        request=request,
        name="audit.html",
        context={"events": events},
    )


@app.post("/api/approve/{draft_id}")
def approve_draft_action(draft_id: str, db: Session = Depends(get_db)):
    approval_svc = ApprovalService(db)
    try:
        job = approval_svc.approve_draft_manually(draft_id, actor="web_admin")
        # Retornar a la página de origen
        draft = db.query(PostDraft).filter(PostDraft.id == draft_id).first()
        if draft and draft.render and draft.render.project_id:
            return RedirectResponse(f"/projects/{draft.render.project_id}", status_code=303)
        return RedirectResponse("/queue", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/media/{render_id}")
def stream_media(render_id: str, db: Session = Depends(get_db)):
    render = db.query(Render).filter(Render.id == render_id).first()
    if not render or not render.video_path:
        raise HTTPException(status_code=404, detail="Video no encontrado")

    path = Path(render.video_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo de video físico no encontrado en disco")

    return FileResponse(path, media_type="video/mp4")


@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    return {
        "status": "ok",
        "projects_count": db.query(Project).count(),
        "renders_count": db.query(Render).count(),
        "queued_jobs": db.query(PublishJob).filter(PublishJob.status == "QUEUED").count(),
    }
