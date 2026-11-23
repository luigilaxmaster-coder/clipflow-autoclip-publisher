"""Aplicación FastAPI segura para AutoClip Publisher — Personal Edition (V2)."""
from __future__ import annotations

import hmac
import json
import logging
import os
import shutil
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, quote
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from clipflow.approvals import ApprovalService
from clipflow.autoclip_adapter import AutoClipAdapter
from clipflow.config import get_settings
from clipflow.connections.service import (
    ConnectionService,
    format_reset_window,
    next_youtube_project_reset_utc,
)
from clipflow.db import get_db, get_db_context, init_db, record_audit
from clipflow.inbox.service import InboxService
from clipflow.ingestion import IngestionService
from clipflow.media_library import MediaLibraryService
from clipflow.media_qa import MediaQA
from clipflow.metadata import MetadataGenerator
from clipflow.niche_router import extract_link_context, route_text_to_profiles
from clipflow.models import (
    AccountConnection,
    AuditEvent,
    AutoReplyRule,
    ConnectionStatus,
    InboxComment,
    JobStatus,
    MediaAsset,
    PostDraft,
    Project,
    PublishJob,
    Render,
    Source,
    utc_now,
)
from clipflow.publishing.native_tiktok import NativeTikTokPublisher
from clipflow.publishing.native_youtube import NativeYouTubePublisher
from clipflow.publishing.postiz import PostizPublisher
from clipflow.publishing.unified import UnifiedPublisher
from clipflow.queue import PublishQueueWorker
from clipflow.scheduler import build_wave_schedule
from clipflow.security import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    authenticate_request,
    create_session_cookie_value,
    generate_csrf_token,
    get_owner_token,
    get_request_csrf_token,
    redact_secrets,
    verify_csrf_token,
    wants_html_response,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_TZ = ZoneInfo("America/Santo_Domingo")
PUBLIC_PATHS = {"/login", "/api/health"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AutoClip Publisher — Personal Edition",
    version="2.0.0",
    description="Centro privado de publicación, programación y gestión multicanal para AutoClip.",
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# -------------------------------------------------------------
# Middleware Global de Autenticación de Propietario + CSRF
# -------------------------------------------------------------
@app.middleware("http")
async def owner_auth_and_csrf_middleware(request: Request, call_next):
    path = request.url.path

    # Rutas públicas estrictamente limitadas: login, health probe y estáticos CSS/JS
    if path in PUBLIC_PATHS or path.startswith("/static/"):
        request.state.csrf_token = get_request_csrf_token(request)
        return await call_next(request)

    is_auth, auth_method = authenticate_request(request)
    if not is_auth:
        if wants_html_response(request):
            next_q = quote(path, safe="/")
            return RedirectResponse(url=f"/login?next={next_q}", status_code=status.HTTP_303_SEE_OTHER)
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Acceso denegado: Se requiere autenticación del propietario único."},
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Protección CSRF obligatoria en mutaciones autenticadas por cookie
    if auth_method == "cookie" and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        owner_token = get_owner_token() or ""
        session_cookie = request.cookies.get(COOKIE_NAME)
        provided_csrf = request.headers.get(CSRF_HEADER_NAME)

        if not provided_csrf:
            ctype = (request.headers.get("content-type") or "").lower()
            if "application/x-www-form-urlencoded" in ctype:
                raw_body = await request.body()
                parsed = parse_qs(raw_body.decode("utf-8", errors="ignore"))
                vals = parsed.get("csrf_token")
                if vals:
                    provided_csrf = vals[0]
            elif "multipart/form-data" in ctype:
                form = await request.form()
                val = form.get("csrf_token")
                if isinstance(val, str):
                    provided_csrf = val

        if not verify_csrf_token(provided_csrf, session_cookie, owner_token):
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Solicitud bloqueada: Token CSRF faltante o inválido."},
            )

    request.state.auth_method = auth_method
    request.state.csrf_token = get_request_csrf_token(request)
    response = await call_next(request)

    # Cabeceras de endurecimiento de seguridad
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


def _render(request: Request, name: str, context: Optional[Dict[str, Any]] = None) -> HTMLResponse:
    ctx = dict(context or {})
    ctx["csrf_token"] = getattr(request.state, "csrf_token", "") or get_request_csrf_token(request)
    return templates.TemplateResponse(request=request, name=name, context=ctx)


def _system_health_snapshot(db: Session) -> Dict[str, Any]:
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    ffmpeg_ok = shutil.which("ffmpeg") is not None
    postiz = PostizPublisher(mock_mode=False)
    postiz_configured = postiz.is_configured
    postiz_online = False
    if postiz_configured:
        postiz_online = postiz.ping()
    return {
        "database_connected": db_ok,
        "ffmpeg_available": ffmpeg_ok,
        "postiz_configured": postiz_configured,
        "postiz_online": postiz_online,
        "status": "healthy" if (db_ok and ffmpeg_ok) else "degraded",
    }


def _youtube_daily_quota_snapshot(db: Session) -> Dict[str, Any]:
    """Calcula el consumo estimado de cuota diaria de YouTube Data API v3 (1,600 unidades/subida sobre 10,000 estándar)."""
    since = utc_now() - timedelta(hours=24)
    recent_yt = (
        db.query(PublishJob)
        .join(PostDraft, PublishJob.post_draft_id == PostDraft.id)
        .filter(
            PostDraft.platform == "youtube",
            PublishJob.status.in_([JobStatus.PUBLISHED, JobStatus.PROCESSING, "REMOTE_PROCESSING", JobStatus.UPLOADING]),
            PublishJob.updated_at >= since,
        )
        .count()
    )
    daily_units = int(os.getenv("YOUTUBE_DAILY_QUOTA_UNITS", "10000"))
    unit_cost = 1600
    max_uploads = max(1, daily_units // unit_cost)
    used_units = recent_yt * unit_cost
    now_ref = utc_now()
    yt_reset = format_reset_window(next_youtube_project_reset_utc(now_ref), now_utc=now_ref)
    return {
        "used_uploads": recent_yt,
        "max_uploads": max_uploads,
        "remaining_uploads": max(0, max_uploads - recent_yt),
        "used_units": used_units,
        "daily_units": daily_units,
        "api_reset_at": yt_reset["at_utc"],
        "api_reset_local": f"{yt_reset['local_label']} (00:00 PT)",
        "api_reset_countdown": yt_reset["countdown_label"],
    }


def _collect_real_analytics(db: Session) -> Dict[str, Any]:
    """
    Recupera métricas reales desde las APIs oficiales conectadas.
    NUNCA inventa cifras: si una API no está autorizada o no devuelve datos,
    asigna explícitamente estado 'SIN_DATOS'.
    """
    published_jobs = (
        db.query(PublishJob)
        .filter(PublishJob.status == JobStatus.PUBLISHED)
        .order_by(PublishJob.created_at.desc())
        .all()
    )

    conn_service = ConnectionService(db)
    yt_conn = conn_service.get_connection("youtube")
    yt_stats_map: Dict[str, Dict[str, Any]] = {}
    yt_source_note = "YouTube no conectado"

    if yt_conn and yt_conn.status == ConnectionStatus.CONNECTED:
        yt_ids = [
            j.remote_post_id
            for j in published_jobs
            if j.post_draft and j.post_draft.platform.lower() == "youtube" and j.remote_post_id
        ]
        if yt_ids:
            pub = UnifiedPublisher(connection_service=conn_service, mock_mode=False)
            yt_stats_map = pub.native_youtube.fetch_video_statistics(yt_ids)
            yt_source_note = "YouTube Data API v3"
        else:
            yt_source_note = "Sin videos publicados con ID oficial de YouTube"

    posts_stats: List[Dict[str, Any]] = []
    verified_views = 0
    verified_likes = 0
    verified_comments = 0
    verified_count = 0
    platform_counts: Dict[str, int] = {}

    for job in published_jobs:
        draft = job.post_draft
        plat = (draft.platform or "desconocido").lower() if draft else "desconocido"
        platform_counts[plat] = platform_counts.get(plat, 0) + 1

        real_st = yt_stats_map.get(job.remote_post_id or "") if plat == "youtube" else None
        if real_st and real_st.get("views") is not None:
            views = int(real_st["views"])
            likes = int(real_st["likes"]) if real_st.get("likes") is not None else 0
            comments = int(real_st["comments"]) if real_st.get("comments") is not None else 0
            engagement = ((likes + comments) / max(views, 1)) * 100.0
            verified_views += views
            verified_likes += likes
            verified_comments += comments
            verified_count += 1
            metrics_status = "REAL"
            source_label = "YouTube Data API v3"
            reason = None
            fetched_at = real_st.get("fetched_at")
        else:
            views = None
            likes = None
            comments = None
            engagement = None
            metrics_status = "SIN_DATOS"
            source_label = "SIN_DATOS"
            fetched_at = None
            if plat == "youtube":
                if not yt_conn or yt_conn.status != ConnectionStatus.CONNECTED:
                    reason = "Conecta y autoriza YouTube OAuth para consultar estadísticas oficiales."
                elif not job.remote_post_id:
                    reason = "Sin video_id oficial de YouTube registrado."
                else:
                    reason = "YouTube API no devolvió estadísticas para este video."
            else:
                reason = f"API oficial de analíticas no autorizada para {plat}."

        posts_stats.append({
            "id": job.id,
            "platform": plat,
            "title": draft.title if draft else "Sin título",
            "created_at": job.created_at.strftime("%Y-%m-%d %H:%M") if job.created_at else "",
            "remote_post_id": job.remote_post_id,
            "views": views,
            "likes": likes,
            "comments": comments,
            "engagement": round(engagement, 2) if engagement is not None else None,
            "url": job.remote_url,
            "metrics_status": metrics_status,
            "source": source_label,
            "reason": reason,
            "fetched_at": fetched_at,
        })

    has_real_metrics = verified_count > 0
    avg_engagement = (
        round(((verified_likes + verified_comments) / max(verified_views, 1)) * 100.0, 2)
        if has_real_metrics
        else None
    )

    return {
        "metrics_status": "REAL" if has_real_metrics else "SIN_DATOS",
        "has_real_metrics": has_real_metrics,
        "verified_posts_count": verified_count,
        "total_published_count": len(published_jobs),
        "total_views": verified_views if has_real_metrics else None,
        "total_likes": verified_likes if has_real_metrics else None,
        "total_comments": verified_comments if has_real_metrics else None,
        "avg_engagement": avg_engagement,
        "platform_counts": platform_counts,
        "source_note": yt_source_note,
        "posts_stats": posts_stats,
    }


# -------------------------------------------------------------
# Autenticación de Propietario Único
# -------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: Optional[str] = None):
    owner_configured = get_owner_token() is not None
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={
            "next_url": next if next.startswith("/") and not next.startswith("//") else "/",
            "error": error,
            "owner_configured": owner_configured,
        },
    )


@app.post("/login")
def login_action(request: Request, token: str = Form(...), next_url: str = Form("/")):
    owner_token = get_owner_token()
    if not owner_token:
        raise HTTPException(
            status_code=503,
            detail="OWNER_TOKEN no configurado en el servidor. Define OWNER_TOKEN en .env o data/.owner.token.",
        )

    if hmac.compare_digest(token.strip(), owner_token):
        safe_next = next_url if (next_url.startswith("/") and not next_url.startswith("//")) else "/"
        session_val = create_session_cookie_value(owner_token)
        csrf_val = generate_csrf_token(session_val, owner_token)
        is_https = request.url.scheme == "https"

        response = RedirectResponse(url=safe_next, status_code=status.HTTP_303_SEE_OTHER)
        response.set_cookie(
            key=COOKIE_NAME,
            value=session_val,
            httponly=True,
            secure=is_https,
            samesite="lax",
            max_age=86400 * 14,
        )
        response.set_cookie(
            key=CSRF_COOKIE_NAME,
            value=csrf_val,
            httponly=False,
            secure=is_https,
            samesite="lax",
            max_age=86400 * 14,
        )
        return response

    return RedirectResponse(url="/login?error=invalid", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/logout")
def logout_action():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie(CSRF_COOKIE_NAME)
    return response


# -------------------------------------------------------------
# Vistas Principales del Dashboard (10 Secciones)
# -------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
@app.get("/overview", response_class=HTMLResponse)
def overview_view(request: Request, db: Session = Depends(get_db)):
    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    renders_count = db.query(Render).count()
    assets_count = db.query(MediaAsset).count()
    published_count = db.query(PublishJob).filter(PublishJob.status == JobStatus.PUBLISHED).count()
    queued_count = db.query(PublishJob).filter(PublishJob.status.in_([JobStatus.QUEUED, JobStatus.SCHEDULED])).count()
    failed_count = (
        db.query(PublishJob)
        .filter(PublishJob.status.in_([JobStatus.FAILED, JobStatus.REQUIRES_ACTION, "ACTION_REQUIRED"]))
        .count()
    )
    connected_apps_count = (
        db.query(AccountConnection).filter(AccountConnection.status == ConnectionStatus.CONNECTED).count()
    )

    recent_jobs = db.query(PublishJob).order_by(PublishJob.created_at.desc()).limit(10).all()
    upcoming_jobs = (
        db.query(PublishJob)
        .filter(PublishJob.status.in_([JobStatus.SCHEDULED, JobStatus.QUEUED]))
        .order_by(PublishJob.scheduled_at_utc.asc().nullslast())
        .limit(5)
        .all()
    )

    # Distribución real por plataforma
    all_jobs = db.query(PublishJob).all()
    platform_breakdown: Dict[str, int] = {}
    daily_counts: Dict[str, int] = {}
    for j in all_jobs:
        plat = j.post_draft.platform.lower() if j.post_draft else "otros"
        platform_breakdown[plat] = platform_breakdown.get(plat, 0) + 1
        if j.created_at:
            day_key = j.created_at.strftime("%Y-%m-%d")
            daily_counts[day_key] = daily_counts.get(day_key, 0) + 1

    health = _system_health_snapshot(db)
    stats = {
        "projects_count": len(projects),
        "renders_count": max(renders_count, assets_count),
        "published_count": published_count,
        "queued_count": queued_count,
        "failed_count": failed_count,
        "connected_apps_count": connected_apps_count,
    }

    return _render(
        request,
        "overview.html",
        {
            "projects": projects,
            "stats": stats,
            "recent_jobs": recent_jobs,
            "upcoming_jobs": upcoming_jobs,
            "platform_breakdown": platform_breakdown,
            "daily_counts": dict(sorted(daily_counts.items())[-14:]),
            "health": health,
            "youtube_quota": _youtube_daily_quota_snapshot(db),
            "provider_accounts": ConnectionService(db).list_provider_accounts(),
        },
    )


@app.get("/connections", response_class=HTMLResponse)
def connections_view(
    request: Request,
    oauth: Optional[str] = None,
    provider: Optional[str] = None,
    oauth_error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    conn_service = ConnectionService(db)
    connections = conn_service.list_connections()
    provider_accounts = conn_service.list_provider_accounts()
    connected_count = sum(1 for c in provider_accounts if c["status"] == ConnectionStatus.CONNECTED)
    youtube_redirect_uri = (os.getenv("YOUTUBE_REDIRECT_URI") or "").strip() or str(
        request.url_for("youtube_oauth_callback")
    )
    tiktok_redirect_uri = (os.getenv("TIKTOK_REDIRECT_URI") or "").strip() or str(
        request.url_for("tiktok_oauth_callback")
    )
    return _render(
        request,
        "connections.html",
        {
            "connections": connections,
            "provider_accounts": provider_accounts,
            "connected_count": connected_count,
            "oauth_status": oauth,
            "oauth_provider": provider,
            "oauth_error": oauth_error,
            "youtube_redirect_uri": youtube_redirect_uri,
            "tiktok_redirect_uri": tiktok_redirect_uri,
        },
    )


@app.get("/publisher", response_class=HTMLResponse)
def publisher_view(request: Request, asset_id: Optional[str] = None, db: Session = Depends(get_db)):
    media_service = MediaLibraryService(db)
    media_service.sync_renders_from_autoclip()
    assets = media_service.list_assets(limit=100)
    selected_asset = media_service.get_asset(asset_id) if asset_id else (assets[0] if assets else None)

    conn_service = ConnectionService(db)
    connections = {c["id"]: c for c in conn_service.list_connections()}
    accounts_by_platform: Dict[str, List[Dict[str, Any]]] = {}
    for acc in conn_service.list_provider_accounts():
        accounts_by_platform.setdefault(acc["provider_id"], []).append(acc)

    return _render(
        request,
        "publisher.html",
        {
            "assets": assets,
            "selected_asset": selected_asset,
            "connections_map": connections,
            "accounts_by_platform": accounts_by_platform,
        },
    )


@app.get("/calendar", response_class=HTMLResponse)
def calendar_view(request: Request, db: Session = Depends(get_db)):
    scheduled_jobs = (
        db.query(PublishJob)
        .filter(
            PublishJob.status.in_([
                JobStatus.QUEUED,
                JobStatus.SCHEDULED,
                JobStatus.UPLOADING,
                JobStatus.PROCESSING,
                JobStatus.PUBLISHED,
            ])
        )
        .order_by(PublishJob.scheduled_at_utc.asc().nullslast(), PublishJob.created_at.desc())
        .limit(100)
        .all()
    )

    calendar_events = []
    for j in scheduled_jobs:
        dt = j.scheduled_at_utc or j.created_at or utc_now()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(LOCAL_TZ)
        render = j.post_draft.render if j.post_draft else None
        asset = db.query(MediaAsset).filter(MediaAsset.render_id == render.id).first() if render else None
        calendar_events.append({
            "id": j.id,
            "title": j.post_draft.title if j.post_draft else f"Job {j.id[:8]}",
            "platform": j.post_draft.platform if j.post_draft else "unknown",
            "status": j.status,
            "date": local_dt.strftime("%Y-%m-%d"),
            "time": local_dt.strftime("%H:%M"),
            "iso_local": local_dt.isoformat(),
            "remote_url": j.remote_url,
            "asset_id": asset.id if asset else None,
            "has_thumb": bool(asset and asset.thumbnail_path),
            "movable": j.status in (JobStatus.SCHEDULED, JobStatus.QUEUED, JobStatus.DRAFT, JobStatus.READY),
        })

    return _render(
        request,
        "calendar.html",
        {
            "scheduled_jobs": scheduled_jobs,
            "calendar_events": calendar_events,
            "today_local": datetime.now(LOCAL_TZ).strftime("%Y-%m-%d"),
        },
    )


@app.get("/media-library", response_class=HTMLResponse)
def media_library_view(request: Request, db: Session = Depends(get_db)):
    media_service = MediaLibraryService(db)
    media_service.sync_renders_from_autoclip()
    assets = media_service.list_assets(limit=100)

    # Enriquecer con estado real de MediaQA del render si existe
    renders_map = {r.id: r for r in db.query(Render).all()}
    enriched = []
    for a in assets:
        r = renders_map.get(a.render_id) if a.render_id else None
        enriched.append({
            "asset": a,
            "qa_status": r.qa_status if r else "SIN_QA",
            "platform": r.target_platform if r else "local",
            "tags": json.loads(a.tags_json) if a.tags_json else [],
        })

    return _render(
        request,
        "media_library.html",
        {"assets": assets, "enriched_assets": enriched},
    )


@app.get("/analytics", response_class=HTMLResponse)
def analytics_view(request: Request, db: Session = Depends(get_db)):
    analytics_data = _collect_real_analytics(db)
    return _render(request, "analytics.html", analytics_data)


@app.get("/inbox", response_class=HTMLResponse)
def inbox_view(request: Request, db: Session = Depends(get_db)):
    inbox_service = InboxService(db)
    comments = inbox_service.list_comments()
    auto_reply_rule = db.query(AutoReplyRule).first()
    yt_conn = ConnectionService(db).get_connection("youtube")
    return _render(
        request,
        "inbox.html",
        {
            "comments": comments,
            "auto_reply_rule": auto_reply_rule,
            "youtube_connected": bool(yt_conn and yt_conn.status == ConnectionStatus.CONNECTED),
        },
    )


@app.get("/automations", response_class=HTMLResponse)
def automations_view(request: Request, db: Session = Depends(get_db)):
    return _render(request, "automations.html")


@app.get("/queue", response_class=HTMLResponse)
def queue_view(request: Request, db: Session = Depends(get_db)):
    jobs = db.query(PublishJob).order_by(PublishJob.created_at.desc()).all()
    counts = {
        "total": len(jobs),
        "queued": sum(1 for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.SCHEDULED)),
        "processing": sum(1 for j in jobs if j.status in (JobStatus.UPLOADING, JobStatus.PROCESSING, "REMOTE_PROCESSING")),
        "published": sum(1 for j in jobs if j.status == JobStatus.PUBLISHED),
        "attention": sum(1 for j in jobs if j.status in (JobStatus.FAILED, JobStatus.REQUIRES_ACTION, "ACTION_REQUIRED")),
    }
    return _render(
        request,
        "queue.html",
        {
            "jobs": jobs,
            "counts": counts,
            "youtube_quota": _youtube_daily_quota_snapshot(db),
            "provider_accounts": ConnectionService(db).list_provider_accounts(),
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_view(request: Request, db: Session = Depends(get_db)):
    backups_dir = Path("data") / "backups"
    backups = []
    if backups_dir.is_dir():
        for b in sorted(backups_dir.glob("clipflow_backup_*.zip"), key=os.path.getmtime, reverse=True):
            backups.append({"name": b.name, "size": b.stat().st_size})
    health = _system_health_snapshot(db)
    return _render(request, "settings.html", {"backups": backups, "health": health})


@app.get("/audit", response_class=HTMLResponse)
def audit_view(request: Request, db: Session = Depends(get_db)):
    events = db.query(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100).all()
    return _render(request, "audit.html", {"events": events})


@app.get("/projects/{project_id}", response_class=HTMLResponse)
def project_detail(project_id: str, request: Request, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return _render(request, "project_detail.html", {"project": project})


# -------------------------------------------------------------
# API Endpoints: Connections Hub & Flujo OAuth 2.0 Completo
# -------------------------------------------------------------
class SaveCredentialsRequest(BaseModel):
    account_name: str
    credentials: Dict[str, Any]
    test_now: bool = True
    connection_id: Optional[str] = None
    create_new: bool = False
    niche_tags: Optional[str] = None
    daily_upload_limit: Optional[int] = None
    is_default: Optional[bool] = None


class UpdateProfileRequest(BaseModel):
    account_name: Optional[str] = None
    niche_tags: Optional[str] = None
    daily_upload_limit: Optional[int] = None
    is_default: Optional[bool] = None


class NicheResolveRequest(BaseModel):
    url: Optional[str] = None
    text: Optional[str] = None
    platforms: Optional[List[str]] = None


@app.post("/api/connections/{provider_id}/save")
def api_save_connection(provider_id: str, payload: SaveCredentialsRequest, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    try:
        res = conn_service.save_connection(
            provider_id=provider_id,
            account_name=payload.account_name,
            credentials=payload.credentials,
            test_now=payload.test_now,
            allow_mock=False,  # Prohibido mock vía API
            connection_id=payload.connection_id,
            create_new=payload.create_new,
            niche_tags=payload.niche_tags,
            daily_upload_limit=payload.daily_upload_limit,
            is_default=payload.is_default,
        )
        return res
    except Exception as e:
        logger.error("Error al guardar conexión %s: %s", provider_id, redact_secrets(str(e)))
        raise HTTPException(status_code=400, detail=redact_secrets(str(e)))


@app.post("/api/connections/profile/{connection_id}/update")
def api_update_connection_profile(connection_id: str, payload: UpdateProfileRequest, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    try:
        profile = conn_service.update_account_profile(
            connection_id=connection_id,
            account_name=payload.account_name,
            niche_tags=payload.niche_tags,
            daily_upload_limit=payload.daily_upload_limit,
            is_default=payload.is_default,
        )
        return {"ok": True, "profile": profile}
    except Exception as e:
        raise HTTPException(status_code=404, detail=redact_secrets(str(e)))


@app.post("/api/connections/profile/{connection_id}/test")
def api_test_profile_connection(connection_id: str, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    conn = conn_service.get_connection_by_id(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Perfil no encontrado")
    return conn_service.test_connection_by_id(conn.provider_id, connection_id=conn.id)


@app.post("/api/connections/profile/{connection_id}/disconnect")
def api_disconnect_profile_connection(connection_id: str, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    conn_service.disconnect_by_id(connection_id)
    return RedirectResponse(url="/connections", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/niche/resolve")
def api_niche_resolve(payload: NicheResolveRequest, db: Session = Depends(get_db)):
    """Analiza un enlace o texto de video e identifica el perfil independiente ideal por nicho."""
    meta: Dict[str, Any] = {}
    ctx_parts = [payload.text or ""]
    if payload.url and payload.url.strip():
        try:
            meta = extract_link_context(payload.url.strip())
            ctx_parts.append(meta.get("context_text", ""))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    routes = route_text_to_profiles(db, " ".join(ctx_parts).strip(), platforms=payload.platforms)
    return {"ok": True, "metadata": meta, "routes": routes}


@app.post("/api/connections/{provider_id}/test")
def api_test_connection(provider_id: str, connection_id: Optional[str] = None, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    return conn_service.test_connection_by_id(provider_id, connection_id=connection_id)


@app.post("/api/connections/{provider_id}/disconnect")
def api_disconnect_connection(provider_id: str, connection_id: Optional[str] = None, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    if connection_id:
        conn_service.disconnect_by_id(connection_id)
    else:
        conn_service.disconnect(provider_id)
    return RedirectResponse(url="/connections", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/connections/youtube/oauth/start")
def api_youtube_oauth_start(
    request: Request,
    connection_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Inicia el flujo interactivo de Google OAuth 2.0 ligado opcionalmente a un perfil independiente."""
    conn_service = ConnectionService(db)
    creds = conn_service.get_decrypted_credentials("youtube", account_ref=connection_id)
    client_id = (creds.get("client_id") or os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (creds.get("client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()

    if not client_id or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Primero guarda YOUTUBE_CLIENT_ID y YOUTUBE_CLIENT_SECRET en Connected Apps.",
        )

    redirect_uri = (os.getenv("YOUTUBE_REDIRECT_URI") or "").strip() or str(
        request.url_for("youtube_oauth_callback")
    )
    state_val = conn_service.create_oauth_state("youtube", redirect_uri, connection_id=connection_id)

    pub = NativeYouTubePublisher(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        mock_mode=False,
    )
    auth_url = pub.get_authorization_url(state=state_val)

    if "application/json" in (request.headers.get("Accept") or "").lower():
        return {"ok": True, "auth_url": auth_url, "redirect_uri": redirect_uri}
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/api/connections/youtube/callback", name="youtube_oauth_callback")
def api_youtube_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Callback oficial de Google OAuth 2.0: valida estado, canjea código, cifra tokens y verifica canal."""
    if error:
        friendly_errors = {
            "access_denied": "Cancelaste el consentimiento en Google. Vuelve a pulsar 'Conectar YouTube' y acepta los permisos solicitados.",
            "org_internal": "La app de Google Cloud está restringida a una organización interna. Cámbiala a Externo y añade tu correo en Usuarios de prueba.",
        }
        err_text = friendly_errors.get(error.strip().lower(), f"Google devolvió el error OAuth: {error}")
        return RedirectResponse(
            url=f"/connections?oauth=error&provider=youtube&oauth_error={quote(err_text)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan parámetros obligatorios 'code' o 'state'.")

    conn_service = ConnectionService(db)
    state_entry = conn_service.consume_oauth_state(state, "youtube")
    if not state_entry:
        raise HTTPException(status_code=400, detail="Estado OAuth inválido, expirado o ya utilizado.")

    target_conn_id = state_entry.connection_id
    creds = conn_service.get_decrypted_credentials("youtube", account_ref=target_conn_id)
    client_id = (creds.get("client_id") or os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
    client_secret = (creds.get("client_secret") or os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()

    pub = NativeYouTubePublisher(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=state_entry.redirect_uri,
        mock_mode=False,
    )
    try:
        token_data = pub.exchange_code(code)
    except Exception as e:
        err_msg = redact_secrets(str(e))
        return RedirectResponse(
            url=f"/connections?oauth=error&provider=youtube&oauth_error={quote(err_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    scopes = token_data.get("scope", "").split() if token_data.get("scope") else None
    result = conn_service.update_oauth_tokens(
        provider_id="youtube",
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in"),
        scopes=scopes,
        verify_now=True,
        client_id=client_id,
        client_secret=client_secret,
        connection_id=target_conn_id,
    )
    record_audit(
        db,
        "AccountConnection",
        result.get("connection_id") or "youtube",
        "owner",
        "oauth_connected" if result["ok"] else "oauth_verify_failed",
        None,
        {"status": result["status"], "account_id": result.get("account_id")},
    )
    db.commit()

    if result["ok"]:
        return RedirectResponse(url="/connections?oauth=success&provider=youtube", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/connections?oauth=error&provider=youtube&oauth_error={quote(result['message'])}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/api/connections/youtube/oauth/refresh")
def api_youtube_oauth_refresh(connection_id: Optional[str] = None, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    return conn_service.refresh_connection_tokens("youtube", connection_id=connection_id)


@app.get("/api/connections/tiktok/oauth/start")
def api_tiktok_oauth_start(
    request: Request,
    connection_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Inicia el flujo OAuth 2.0 interactivo de TikTok ligado opcionalmente a un perfil independiente."""
    conn_service = ConnectionService(db)
    creds = conn_service.get_decrypted_credentials("tiktok", account_ref=connection_id)
    client_key = (creds.get("client_key") or os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
    client_secret = (creds.get("client_secret") or os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()

    if not client_key or not client_secret:
        raise HTTPException(
            status_code=400,
            detail="Primero guarda TIKTOK_CLIENT_KEY y TIKTOK_CLIENT_SECRET en Connected Apps.",
        )

    redirect_uri = (os.getenv("TIKTOK_REDIRECT_URI") or "").strip() or str(
        request.url_for("tiktok_oauth_callback")
    )
    state_val = conn_service.create_oauth_state("tiktok", redirect_uri, connection_id=connection_id)

    pub = NativeTikTokPublisher(
        client_key=client_key,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        mock_mode=False,
    )
    auth_url = pub.get_authorization_url(state=state_val)

    if "application/json" in (request.headers.get("Accept") or "").lower():
        return {"ok": True, "auth_url": auth_url, "redirect_uri": redirect_uri}
    return RedirectResponse(url=auth_url, status_code=status.HTTP_302_FOUND)


@app.get("/api/connections/tiktok/callback", name="tiktok_oauth_callback")
def api_tiktok_oauth_callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Callback oficial de TikTok OAuth 2.0: valida state, canjea código, cifra tokens y verifica perfil."""
    if error:
        err_text = redact_secrets(error_description or f"TikTok devolvió el error OAuth: {error}")
        return RedirectResponse(
            url=f"/connections?oauth=error&provider=tiktok&oauth_error={quote(err_text)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    if not code or not state:
        raise HTTPException(status_code=400, detail="Faltan parámetros obligatorios 'code' o 'state' de TikTok.")

    conn_service = ConnectionService(db)
    state_entry = conn_service.consume_oauth_state(state, "tiktok")
    if not state_entry:
        raise HTTPException(status_code=400, detail="Estado OAuth de TikTok inválido, expirado o ya utilizado.")

    target_conn_id = state_entry.connection_id
    creds = conn_service.get_decrypted_credentials("tiktok", account_ref=target_conn_id)
    client_key = (creds.get("client_key") or os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
    client_secret = (creds.get("client_secret") or os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()

    pub = NativeTikTokPublisher(
        client_key=client_key,
        client_secret=client_secret,
        redirect_uri=state_entry.redirect_uri,
        mock_mode=False,
    )
    try:
        token_data = pub.exchange_code(code)
    except Exception as e:
        err_msg = redact_secrets(str(e))
        return RedirectResponse(
            url=f"/connections?oauth=error&provider=tiktok&oauth_error={quote(err_msg)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    raw_scope = token_data.get("scope", "")
    scopes = [s.strip() for s in raw_scope.replace(",", " ").split() if s.strip()] if raw_scope else None
    result = conn_service.update_oauth_tokens(
        provider_id="tiktok",
        access_token=token_data.get("access_token", ""),
        refresh_token=token_data.get("refresh_token"),
        expires_in=token_data.get("expires_in"),
        scopes=scopes,
        verify_now=True,
        extra_fields={
            "client_key": client_key,
            "client_secret": client_secret,
            "open_id": token_data.get("open_id"),
        },
        connection_id=target_conn_id,
    )
    record_audit(
        db,
        "AccountConnection",
        result.get("connection_id") or "tiktok",
        "owner",
        "oauth_connected" if result["ok"] else "oauth_verify_failed",
        None,
        {"status": result["status"], "account_id": result.get("account_id")},
    )
    db.commit()

    if result["ok"]:
        return RedirectResponse(url="/connections?oauth=success&provider=tiktok", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse(
        url=f"/connections?oauth=error&provider=tiktok&oauth_error={quote(result['message'])}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/api/connections/tiktok/oauth/refresh")
def api_tiktok_oauth_refresh(connection_id: Optional[str] = None, db: Session = Depends(get_db)):
    conn_service = ConnectionService(db)
    return conn_service.refresh_connection_tokens("tiktok", connection_id=connection_id)


# -------------------------------------------------------------
# API Endpoints: Media Library, Ingesta & Videos
# -------------------------------------------------------------
@app.post("/api/media/sync")
def api_sync_media(db: Session = Depends(get_db)):
    service = MediaLibraryService(db)
    service.sync_renders_from_autoclip()
    return RedirectResponse(url="/media-library", status_code=status.HTTP_303_SEE_OTHER)


class ImportFolderRequest(BaseModel):
    folder_path: str


@app.post("/api/media/import-folder")
def api_import_media_folder(payload: ImportFolderRequest, db: Session = Depends(get_db)):
    """Importa clips MP4/MOV/MKV desde una carpeta permitida dentro del workspace."""
    workspace_root = Path.cwd().resolve()
    raw_input = Path(payload.folder_path.strip())
    target = (workspace_root / raw_input).resolve() if not raw_input.is_absolute() else raw_input.resolve()

    # Prevenir acceso fuera del workspace permitido
    allowed_roots = [workspace_root, (workspace_root / "clips_para_seleccion").resolve(), (workspace_root / "runs").resolve()]
    if not any(target == r or r in target.parents for r in allowed_roots):
        raise HTTPException(status_code=403, detail="La carpeta debe residir dentro del workspace de AutoClip.")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"Carpeta no encontrada: {target}")

    imported = MediaLibraryService(db).scan_local_directory(str(target))
    return {"ok": True, "imported": imported, "folder": str(target)}


class IngestUrlRequest(BaseModel):
    source_url: str
    platforms: List[str] = ["youtube_shorts"]
    portrait_style: str = "podcast"
    template: str = "classic"
    auto_route_niche: bool = True
    account_selections: Optional[Dict[str, str]] = None


def _run_background_autoclip_project(
    project_id: str,
    source_url: str,
    platforms: List[str],
    portrait_style: str,
    template: str,
    account_selections: Optional[Dict[str, str]] = None,
) -> None:
    try:
        adapter = AutoClipAdapter()
        qa = MediaQA()
        meta_gen = MetadataGenerator()
        res = adapter.run_produce_python(
            source=source_url,
            platforms=platforms,
            portrait_style=portrait_style,
            template=template,
        )
        variants = adapter.extract_output_variants(res)
        with get_db_context() as bg_db:
            proj = bg_db.query(Project).filter(Project.id == project_id).first()
            if not proj:
                return
            proj.autoclip_project_id = res.get("project_id")
            proj.upstream_version = res.get("upstream_version")
            conn_svc = ConnectionService(bg_db)
            for idx, var in enumerate(variants, start=1):
                vid_path = var.get("video_path")
                if not vid_path or not Path(vid_path).is_file():
                    continue
                rep = qa.validate_clip(vid_path, platform=var.get("strategy_id", "youtube_shorts"))
                render = Render(
                    project_id=proj.id,
                    output_variant_id=var.get("output_variant_id"),
                    studio_job_id=Path(vid_path).stem,
                    target_platform=var.get("strategy_id", "youtube_shorts"),
                    video_path=str(Path(vid_path).resolve()),
                    cover_path=var.get("cover_path"),
                    kit_path=var.get("kit_path"),
                    duration_sec=rep.duration_sec,
                    width=rep.width,
                    height=rep.height,
                    qa_status=rep.overall_status,
                    qa_json=json.dumps(rep.to_dict(), ensure_ascii=False),
                )
                bg_db.add(render)
                bg_db.flush()
                title_txt = var.get("title") or f"Clip {idx}"
                desc_txt = var.get("description")
                for d in meta_gen.generate_drafts(
                    render_id=render.id,
                    base_title=title_txt,
                    base_description=desc_txt,
                    tags=var.get("tags") or [],
                ):
                    sel = (account_selections or {}).get(d.platform)
                    if not sel:
                        match = conn_svc.resolve_account_by_niche(d.platform, f"{title_txt} {desc_txt or ''}")
                        if match and match.get("connection"):
                            sel = match["connection"]["connection_id"]
                    d.account_id = sel
                    bg_db.add(d)
            proj.workflow_status = "READY_TO_REVIEW"
            bg_db.commit()
            MediaLibraryService(bg_db).sync_renders_from_autoclip()
    except Exception as e:
        logger.error("Fallo en producción en segundo plano (%s): %s", project_id, redact_secrets(str(e)))
        try:
            with get_db_context() as bg_db:
                proj = bg_db.query(Project).filter(Project.id == project_id).first()
                if proj:
                    proj.workflow_status = "FAILED"
                    bg_db.commit()
        except Exception:
            pass


@app.post("/api/projects/ingest-url")
def api_ingest_project_url(payload: IngestUrlRequest, db: Session = Depends(get_db)):
    """Valida una URL HTTPS, detecta nicho -> perfil destino e inicia AutoClip en segundo plano."""
    ingestion = IngestionService()
    try:
        src = ingestion.process_source(source_input=payload.source_url, rights_confirmed=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=redact_secrets(str(e)))

    resolved_routes: Dict[str, Any] = {}
    selections = dict(payload.account_selections or {})
    if payload.auto_route_niche:
        try:
            meta = extract_link_context(src.canonical_source)
            target_plats = ["youtube" if "youtube" in p else p for p in payload.platforms]
            resolved_routes = route_text_to_profiles(db, meta.get("context_text", ""), platforms=target_plats)
            for plat, info in resolved_routes.items():
                if info.get("found") and plat not in selections:
                    selections[plat] = info["connection_id"]
        except Exception:
            pass

    db.add(src)
    db.flush()
    proj = Project(source_id=src.id, workflow_status="ANALYZING")
    db.add(proj)
    db.flush()
    record_audit(
        db,
        "Project",
        proj.id,
        "owner",
        "web_ingest_url",
        None,
        {"source": src.canonical_source, "routes": selections},
    )
    db.commit()

    t = threading.Thread(
        target=_run_background_autoclip_project,
        args=(proj.id, src.canonical_source, payload.platforms, payload.portrait_style, payload.template, selections),
        daemon=True,
    )
    t.start()
    return {
        "ok": True,
        "project_id": proj.id,
        "workflow_status": proj.workflow_status,
        "niche_routes": resolved_routes,
    }


@app.get("/api/media/asset/{asset_id}")
def stream_asset(asset_id: str, db: Session = Depends(get_db)):
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if not asset or not asset.file_path:
        raise HTTPException(status_code=404, detail="Recurso multimedia no encontrado")
    path = Path(asset.file_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo físico no encontrado en disco")
    return FileResponse(path, media_type="video/mp4")


@app.get("/api/media/thumbnail/{asset_id}")
def stream_thumbnail(asset_id: str, db: Session = Depends(get_db)):
    asset = db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
    if not asset or not asset.thumbnail_path:
        raise HTTPException(status_code=404, detail="Miniatura no encontrada")
    path = Path(asset.thumbnail_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo físico de miniatura no encontrado")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/media/{render_id}")
def stream_media_legacy(render_id: str, db: Session = Depends(get_db)):
    render = db.query(Render).filter(Render.id == render_id).first()
    if not render or not render.video_path:
        raise HTTPException(status_code=404, detail="Video no encontrado")
    path = Path(render.video_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Archivo de video físico no encontrado en disco")
    return FileResponse(path, media_type="video/mp4")


# -------------------------------------------------------------
# API Endpoints: Publisher, Calendar & Queue
# -------------------------------------------------------------
class DispatchRequest(BaseModel):
    asset_id: Optional[str] = None
    video_path: str
    platforms: List[str]
    title: str
    description: Optional[str] = None
    hashtags: Optional[str] = None
    visibility: str = "private"
    scheduled_time: Optional[str] = None
    action: str = "publish_now"
    platform_overrides: Optional[Dict[str, Dict[str, Any]]] = None
    account_selections: Optional[Dict[str, str]] = None
    auto_route_niche: bool = False


@app.post("/api/publisher/dispatch")
def api_publisher_dispatch(payload: DispatchRequest, db: Session = Depends(get_db)):
    if not payload.platforms:
        raise HTTPException(status_code=400, detail="Debe seleccionarse al menos una plataforma.")

    path = Path(payload.video_path)
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"El archivo de video no existe: {payload.video_path}")

    render = None
    if payload.asset_id:
        asset = db.query(MediaAsset).filter(MediaAsset.id == payload.asset_id).first()
        if asset and asset.render_id:
            render = db.query(Render).filter(Render.id == asset.render_id).first()

    if not render:
        render = db.query(Render).filter(Render.video_path == payload.video_path).first()
        if not render:
            project = db.query(Project).first()
            if not project:
                src = Source(source_kind="file", canonical_source=payload.video_path, local_path=payload.video_path)
                db.add(src)
                db.flush()
                project = Project(source_id=src.id, workflow_status="COMPLETED")
                db.add(project)
                db.flush()

            render = Render(
                project_id=project.id,
                target_platform=payload.platforms[0],
                video_path=payload.video_path,
                qa_status="PASSED",
            )
            db.add(render)
            db.flush()

    jobs_created = 0
    created_ids: List[str] = []
    conn_service = ConnectionService(db)
    publisher = UnifiedPublisher(connection_service=conn_service, mock_mode=False)

    for plat in payload.platforms:
        plat_key = plat.lower().strip()
        override = (payload.platform_overrides or {}).get(plat_key, {})

        full_title = (override.get("title") or payload.title).strip()
        if plat_key == "youtube":
            full_title = full_title[:100]

        desc_parts = [override.get("description") or payload.description or ""]
        tags_str = (override.get("hashtags") or payload.hashtags or "").strip()
        if tags_str and tags_str not in desc_parts[0]:
            desc_parts.append(tags_str)
        final_desc = "\n\n".join(p for p in desc_parts if p).strip()

        vis = (override.get("visibility") or payload.visibility or "private").strip()
        if plat_key == "tiktok" and vis == "private":
            vis = "SELF_ONLY"

        selected_account = (payload.account_selections or {}).get(plat_key) or override.get("account_id")
        if not selected_account and payload.auto_route_niche:
            niche_match = conn_service.resolve_account_by_niche(plat_key, f"{full_title} {final_desc}")
            if niche_match and niche_match.get("connection"):
                selected_account = niche_match["connection"]["connection_id"]

        draft = PostDraft(
            render_id=render.id,
            platform=plat_key,
            account_id=selected_account or None,
            title=full_title,
            description=final_desc,
            hashtags_json=json.dumps(tags_str.split()) if tags_str else None,
            visibility=vis,
            approved_at=utc_now() if payload.action != "draft" else None,
            approval_actor="publisher_ui",
        )
        db.add(draft)
        db.flush()

        idemp = f"{draft.id}-{plat_key}-{uuid.uuid4().hex[:8]}"

        sched_dt = None
        if payload.action == "draft":
            job_status = JobStatus.DRAFT
        elif payload.scheduled_time:
            try:
                parsed = datetime.fromisoformat(payload.scheduled_time.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=LOCAL_TZ)
                sched_dt = parsed.astimezone(timezone.utc)
                job_status = JobStatus.SCHEDULED
            except Exception:
                raise HTTPException(status_code=400, detail="Formato de fecha/hora programada inválido.")
        else:
            job_status = JobStatus.QUEUED

        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key=idemp,
            scheduled_at_utc=sched_dt,
            status=job_status,
        )
        db.add(job)
        db.flush()
        created_ids.append(job.id)
        jobs_created += 1

    db.commit()

    if payload.action == "publish_now" and not payload.scheduled_time:
        try:
            worker = PublishQueueWorker(lambda: db, publisher)
            for _ in range(jobs_created):
                worker.process_due_job(db)
        except Exception as e:
            logger.warning("Fallo al procesar inmediatamente con worker: %s", redact_secrets(str(e)))

    return {"ok": True, "jobs_created": jobs_created, "job_ids": created_ids}


class WaveDispatchRequest(BaseModel):
    asset_ids: List[str]
    platform: str = "youtube"
    connection_id: Optional[str] = None
    visibility: str = "private"


@app.post("/api/automation/wave-dispatch")
def api_automation_wave_dispatch(payload: WaveDispatchRequest, db: Session = Depends(get_db)):
    """
    Automatización en 2 Olas:
    Publica ahora la mitad posible de la cuota diaria de la cuenta y programa la otra mitad
    automáticamente en los mejores horarios pico de America/Santo_Domingo.
    """
    if not payload.asset_ids:
        raise HTTPException(status_code=400, detail="Debe indicarse al menos un asset_id.")

    plat = payload.platform.lower().strip()
    conn_svc = ConnectionService(db)
    conn = conn_svc.get_connection(plat, account_ref=payload.connection_id)
    if not conn:
        raise HTTPException(status_code=400, detail=f"No existe perfil conectado para {plat}.")

    acc_info = conn_svc._serialize_account(conn)
    assets = db.query(MediaAsset).filter(MediaAsset.id.in_(payload.asset_ids)).all()
    assets_map = {a.id: a for a in assets}
    ordered_assets = [assets_map[aid] for aid in payload.asset_ids if aid in assets_map]
    if not ordered_assets:
        raise HTTPException(status_code=404, detail="No se encontraron los clips solicitados.")

    plan = build_wave_schedule(
        total_clips=len(ordered_assets),
        remaining_today=acc_info["remaining_today"],
        daily_limit=acc_info["daily_upload_limit"],
    )

    vis = (payload.visibility or "private").strip()
    if plat == "tiktok" and vis == "private":
        vis = "SELF_ONLY"

    created_summary = []
    for asset, slot in zip(ordered_assets, plan):
        render = db.query(Render).filter(Render.id == asset.render_id).first() if asset.render_id else None
        if not render:
            proj = db.query(Project).first()
            if not proj:
                src = Source(source_kind="file", canonical_source=asset.file_path, local_path=asset.file_path)
                db.add(src)
                db.flush()
                proj = Project(source_id=src.id, workflow_status="COMPLETED")
                db.add(proj)
                db.flush()
            render = Render(project_id=proj.id, target_platform=plat, video_path=asset.file_path, qa_status="PASSED")
            db.add(render)
            db.flush()

        draft = PostDraft(
            render_id=render.id,
            platform=plat,
            account_id=conn.id,
            title=asset.title[:100] if plat == "youtube" else asset.title,
            description=f"{asset.title}\n\n#Shorts",
            visibility=vis,
            approved_at=utc_now(),
            approval_actor="wave_automation",
        )
        db.add(draft)
        db.flush()

        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key=f"wave-{draft.id}-{uuid.uuid4().hex[:8]}",
            scheduled_at_utc=slot["scheduled_at_utc"],
            status=slot["status"],
        )
        db.add(job)
        db.flush()
        created_summary.append({
            "job_id": job.id,
            "asset_id": asset.id,
            "title": draft.title,
            "wave": slot["wave"],
            "status": job.status,
            "scheduled_local": slot["scheduled_local"],
            "label": slot["label"],
        })

    record_audit(
        db,
        "AccountConnection",
        conn.id,
        "wave_automation",
        "wave_dispatch",
        None,
        {"count": len(created_summary), "platform": plat},
    )
    db.commit()
    return {
        "ok": True,
        "account_name": acc_info["account_name"],
        "remaining_today_before": acc_info["remaining_today"],
        "daily_limit": acc_info["daily_upload_limit"],
        "jobs": created_summary,
    }


class RescheduleRequest(BaseModel):
    scheduled_time: str  # ISO local (America/Santo_Domingo) o con timezone explícito


@app.post("/api/jobs/{job_id}/reschedule")
def api_reschedule_job(job_id: str, payload: RescheduleRequest, db: Session = Depends(get_db)):
    """Reprograma un trabajo pendiente validando fecha y convirtiendo desde America/Santo_Domingo a UTC."""
    job = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if job.status not in (JobStatus.SCHEDULED, JobStatus.QUEUED, JobStatus.DRAFT, JobStatus.READY):
        raise HTTPException(
            status_code=409,
            detail=f"No se puede reprogramar un trabajo en estado {job.status}.",
        )
    try:
        parsed = datetime.fromisoformat(payload.scheduled_time.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=LOCAL_TZ)
        utc_dt = parsed.astimezone(timezone.utc)
    except Exception:
        raise HTTPException(status_code=400, detail="Formato ISO de fecha/hora inválido.")

    old_dt = job.scheduled_at_utc.isoformat() if job.scheduled_at_utc else None
    job.scheduled_at_utc = utc_dt
    job.status = JobStatus.SCHEDULED
    job.updated_at = utc_now()
    record_audit(db, "PublishJob", job.id, "owner", "rescheduled", {"scheduled_at_utc": old_dt}, {"scheduled_at_utc": utc_dt.isoformat()})
    db.commit()
    return {"ok": True, "scheduled_at_utc": utc_dt.isoformat(), "status": job.status}


@app.post("/api/jobs/{job_id}/retry")
def api_retry_job(job_id: str, db: Session = Depends(get_db)):
    job = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if job.status == JobStatus.PUBLISHED:
        raise HTTPException(status_code=409, detail="El trabajo ya está publicado; no se reintentará para evitar duplicados.")
    job.status = JobStatus.QUEUED
    job.last_error = None
    job.updated_at = utc_now()
    record_audit(db, "PublishJob", job.id, "owner", "manual_retry", None, {"status": JobStatus.QUEUED})
    db.commit()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel_job(job_id: str, db: Session = Depends(get_db)):
    """Cancela una tarea local pendiente. Nunca elimina videos ya publicados en YouTube."""
    job = db.query(PublishJob).filter(PublishJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Trabajo no encontrado")
    if job.status in (JobStatus.PUBLISHED, JobStatus.PROCESSING, "REMOTE_PROCESSING"):
        raise HTTPException(
            status_code=409,
            detail="No se puede cancelar una publicación ya subida o publicada en la plataforma remota.",
        )

    platform = job.post_draft.platform.lower() if job.post_draft else ""
    if job.remote_post_id and platform != "youtube":
        pub = UnifiedPublisher(connection_service=ConnectionService(db), mock_mode=False)
        pub.cancel_scheduled(job.remote_post_id, platform=platform)

    job.status = JobStatus.CANCELLED
    job.updated_at = utc_now()
    record_audit(db, "PublishJob", job.id, "owner", "cancelled", None, {"status": JobStatus.CANCELLED})
    db.commit()
    return {"ok": True}


@app.post("/api/worker/tick")
def api_worker_tick(db: Session = Depends(get_db)):
    publisher = UnifiedPublisher(connection_service=ConnectionService(db), mock_mode=False)
    worker = PublishQueueWorker(lambda: db, publisher)
    job = worker.process_due_job(db)
    return {"ok": True, "processed_job_id": job.id if job else None}


@app.post("/api/approve/{draft_id}")
def approve_draft_action(draft_id: str, db: Session = Depends(get_db)):
    approval_svc = ApprovalService(db)
    try:
        approval_svc.approve_draft_manually(draft_id, actor="web_admin")
        draft = db.query(PostDraft).filter(PostDraft.id == draft_id).first()
        if draft and draft.render and draft.render.project_id:
            return RedirectResponse(f"/projects/{draft.render.project_id}", status_code=303)
        return RedirectResponse("/queue", status_code=303)
    except Exception as e:
        raise HTTPException(status_code=400, detail=redact_secrets(str(e)))


# -------------------------------------------------------------
# API Endpoints: Analytics Reales
# -------------------------------------------------------------
@app.get("/api/analytics/summary")
def api_analytics_summary(db: Session = Depends(get_db)):
    return _collect_real_analytics(db)


# -------------------------------------------------------------
# API Endpoints: Inbox & Respuestas Automáticas
# -------------------------------------------------------------
class ReplyRequest(BaseModel):
    reply_text: str


@app.post("/api/inbox/reply/{comment_id}")
def api_inbox_reply(comment_id: str, payload: ReplyRequest, db: Session = Depends(get_db)):
    inbox_service = InboxService(db)
    success = inbox_service.reply_to_comment(comment_id, payload.reply_text)
    if success:
        return {"ok": True}
    return {"ok": False, "error": "No se pudo enviar la respuesta a través de la API oficial autorizada."}


class AutoReplyConfigRequest(BaseModel):
    keyword_filter: Optional[str] = None
    cooldown_minutes: int = 60
    reply_template: str
    is_enabled: bool = True


@app.post("/api/inbox/auto-reply/config")
def api_auto_reply_config(payload: AutoReplyConfigRequest, db: Session = Depends(get_db)):
    rule = db.query(AutoReplyRule).first()
    if not rule:
        rule = AutoReplyRule()
        db.add(rule)
    rule.keyword_filter = payload.keyword_filter
    rule.cooldown_minutes = max(5, payload.cooldown_minutes)
    rule.reply_template = payload.reply_template
    rule.is_enabled = payload.is_enabled
    db.commit()
    return {"ok": True}


@app.post("/api/inbox/auto-reply/toggle")
def api_auto_reply_toggle(db: Session = Depends(get_db)):
    rule = db.query(AutoReplyRule).first()
    if rule:
        rule.is_enabled = not rule.is_enabled
        db.commit()
    return RedirectResponse("/inbox", status_code=status.HTTP_303_SEE_OTHER)


@app.post("/api/inbox/sync")
def api_inbox_sync(db: Session = Depends(get_db)):
    """Sincroniza comentarios reales de los últimos videos publicados en YouTube."""
    inbox_service = InboxService(db)
    yt_jobs = (
        db.query(PublishJob)
        .join(PostDraft, PublishJob.post_draft_id == PostDraft.id)
        .filter(
            PostDraft.platform == "youtube",
            PublishJob.status == JobStatus.PUBLISHED,
            PublishJob.remote_post_id != None,
        )
        .order_by(PublishJob.created_at.desc())
        .limit(5)
        .all()
    )
    imported = 0
    for j in yt_jobs:
        if j.remote_post_id:
            imported += inbox_service.sync_comments_from_youtube(j.remote_post_id)
    return RedirectResponse(url=f"/inbox?synced={imported}", status_code=status.HTTP_303_SEE_OTHER)


# -------------------------------------------------------------
# API Endpoints: Sistema, Backups & Health Check
# -------------------------------------------------------------
@app.post("/api/system/backup")
def api_system_backup():
    try:
        from scripts.backup import create_backup
        create_backup()
        return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)
    except Exception as e:
        logger.error("Error en respaldo: %s", redact_secrets(str(e)))
        raise HTTPException(status_code=500, detail="Error al crear respaldo.")


@app.post("/api/system/restore")
def api_system_restore(backup_name: str = Form(...)):
    # Prevenir path traversal
    safe_name = Path(backup_name).name
    target = Path("data") / "backups" / safe_name
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo de respaldo no encontrado")
    from scripts.restore import restore_backup
    success = restore_backup(str(target))
    if not success:
        raise HTTPException(status_code=500, detail="Fallo durante la restauración de respaldo")
    return RedirectResponse("/settings", status_code=status.HTTP_303_SEE_OTHER)


@app.get("/api/health")
def api_health(db: Session = Depends(get_db)):
    snap = _system_health_snapshot(db)
    return {
        "status": snap["status"],
        "timestamp": utc_now().isoformat(),
        "database_connected": snap["database_connected"],
        "ffmpeg_available": snap["ffmpeg_available"],
        "postiz_online": snap["postiz_online"],
        "version": "2.0.0",
        "environment": "personal_edition",
    }


@app.get("/api/status")
def api_status(db: Session = Depends(get_db)):
    snap = _system_health_snapshot(db)
    return {
        "status": "ok",
        "health": snap,
        "projects_count": db.query(Project).count(),
        "renders_count": db.query(Render).count(),
        "connected_apps_count": db.query(AccountConnection).filter(AccountConnection.status == ConnectionStatus.CONNECTED).count(),
        "queued_jobs": db.query(PublishJob).filter(PublishJob.status.in_([JobStatus.QUEUED, JobStatus.SCHEDULED])).count(),
        "published_jobs": db.query(PublishJob).filter(PublishJob.status == JobStatus.PUBLISHED).count(),
        "failed_jobs": db.query(PublishJob).filter(PublishJob.status.in_([JobStatus.FAILED, JobStatus.REQUIRES_ACTION, "ACTION_REQUIRED"])).count(),
        "youtube_quota": _youtube_daily_quota_snapshot(db),
    }
