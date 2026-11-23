"""CLI interactiva y automatizada para clipflow."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

from clipflow.approvals import ApprovalService
from clipflow.autoclip_adapter import AutoClipAdapter
from clipflow.config import get_settings
from clipflow.db import get_db_context, init_db, record_audit
from clipflow.ingestion import IngestionService
from clipflow.media_qa import MediaQA, find_ffprobe
from clipflow.metadata import MetadataGenerator
from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.queue import PublishQueueWorker
from clipflow.reporting import ReportingService
from clipflow.scheduler import parse_schedule_to_utc

logger = logging.getLogger("clipflow")


def setup_cli_logging(level: str = "INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    """Comprueba el entorno: Python, FFmpeg, Whisper, AutoClip y Upload-Post."""
    print("=== ClipFlow Doctor - Diagnóstico del Sistema ===")
    ok = True

    # 1. Python
    print(f"✓ Python: {sys.version.split()[0]} ({sys.executable})")

    # 2. FFmpeg / FFprobe
    try:
        probe = find_ffprobe()
        print(f"✓ FFprobe: {probe}")
    except Exception as e:
        print(f"✗ FFprobe: NO DISPONIBLE ({e})")
        ok = False

    # 3. AutoClip Upstream
    try:
        from backend import __version__ as ac_ver
        print(f"✓ AutoClip upstream: v{ac_ver} integrado")
    except Exception as e:
        print(f"✗ AutoClip upstream: error al importar ({e})")
        ok = False

    # 4. Whisper / ASR
    try:
        import faster_whisper
        print(f"✓ Whisper local: faster-whisper {faster_whisper.__version__} instalado")
    except ImportError:
        print("⚠ Whisper local: faster-whisper no instalado (requerido solo si el video no tiene .srt)")

    # 5. Upload-Post
    publisher = UploadPostPublisher()
    if publisher.is_configured:
        print("✓ Upload-Post: API Key configurada")
        try:
            profiles = publisher.list_profiles()
            if profiles:
                print(f"✓ Perfiles Upload-Post disponibles: {[p.get('username') for p in profiles]}")
            else:
                print("⚠ Upload-Post: API Key válida pero no se encontraron perfiles creados.")
        except Exception as e:
            print(f"⚠ Upload-Post: error al consultar perfiles ({e})")
    else:
        print("⚠ Upload-Post: No configurado (establece UPLOAD_POST_API_KEY). Publicaciones reales estarán en modo mock/draft.")

    # 6. Base de datos
    try:
        init_db()
        print(f"✓ Base de datos: Inicializada en {get_settings().app.database_url}")
    except Exception as e:
        print(f"✗ Base de datos: Error al conectar ({e})")
        ok = False

    return 0 if ok else 1


def cmd_accounts(args: argparse.Namespace) -> int:
    """Muestra los perfiles y cuentas conectadas de Upload-Post."""
    publisher = UploadPostPublisher()
    if not publisher.is_configured:
        print("Upload-Post no está configurado. Define UPLOAD_POST_API_KEY o ejecuta en modo mock.")
        return 1

    try:
        profiles = publisher.list_profiles()
        if not profiles:
            print("No se encontraron perfiles en Upload-Post.")
            return 0

        print("Perfiles y plataformas conectadas en Upload-Post:")
        for p in profiles:
            user = p.get("username", "desconocido")
            connected = p.get("connected_platforms", [])
            reconnect = p.get("reconnect_platforms", [])
            print(f"  ● {user:15} Conectadas: {', '.join(connected) if connected else 'Ninguna'}")
            if reconnect:
                print(f"    ⚠ Requieren reconexión: {', '.join(reconnect)}")
        return 0
    except Exception as e:
        print(f"Error consultando perfiles: {e}", file=sys.stderr)
        return 1


def cmd_create(args: argparse.Namespace) -> int:
    """Procesa una fuente, genera clips 9:16, ejecuta QA y genera borradores de publicación."""
    init_db()
    settings = get_settings()
    qa = MediaQA()
    ingestion = IngestionService(qa)
    adapter = AutoClipAdapter()
    meta_gen = MetadataGenerator()

    platforms = args.platform or ["youtube_shorts", "tiktok"]
    print(f"Iniciando procesamiento de fuente: {args.source}")
    print(f"Plataformas objetivo: {', '.join(platforms)}")

    with get_db_context() as db:
        # 1. Ingesta
        try:
            source = ingestion.process_source(
                source_input=args.source,
                srt_path=args.srt,
                rights_confirmed=True,
            )
            db.add(source)
            db.flush()
        except Exception as e:
            print(f"Error en ingesta de fuente: {e}", file=sys.stderr)
            return 2

        # 2. Proyecto
        project = Project(
            source_id=source.id,
            workflow_status="INGESTED",
        )
        db.add(project)
        db.flush()
        record_audit(db, "Project", project.id, "cli", "create_project", None, {"source": args.source})

        print(f"Proyecto ClipFlow creado: {project.id}")

        # 3. Invocar AutoClip Studio / Produce
        print("Ejecutando motor de análisis y renderizado AutoClip...")
        project.workflow_status = "ANALYZING"
        db.commit()

        try:
            res = adapter.run_produce_python(
                source=source.canonical_source,
                platforms=platforms,
                srt_path=args.srt,
                portrait_style=args.portrait_style or settings.render.portrait_style,
                template=args.template or settings.render.template,
            )
            project.autoclip_project_id = res.get("project_id")
            project.upstream_version = res.get("upstream_version")
            raw_variants = adapter.extract_output_variants(res)
        except Exception as e:
            project.workflow_status = "FAILED"
            db.commit()
            print(f"Error durante la producción en AutoClip: {e}", file=sys.stderr)
            return 1

        print(f"AutoClip generó {len(raw_variants)} variantes de salida.")
        project.workflow_status = "QA_PENDING"
        db.flush()

        # 4. QA y Creación de Renders y Borradores
        created_renders = 0
        approval_svc = ApprovalService(db)

        for var in raw_variants:
            vid_path = var.get("video_path")
            if not vid_path or not Path(vid_path).is_file():
                continue

            # Ejecutar MediaQA estricto
            report = qa.validate_clip(vid_path, platform=var.get("strategy_id", "youtube_shorts"))

            p_vid = Path(vid_path)
            render = Render(
                project_id=project.id,
                output_variant_id=var.get("output_variant_id"),
                studio_job_id=p_vid.stem,
                target_platform=var.get("strategy_id", "youtube_shorts"),
                video_path=str(p_vid.resolve()),
                cover_path=var.get("cover_path"),
                kit_path=var.get("kit_path"),
                duration_sec=report.duration_sec,
                width=report.width,
                height=report.height,
                qa_status=report.overall_status,
                qa_json=json.dumps(report.to_dict(), ensure_ascii=False),
            )
            db.add(render)
            db.flush()
            created_renders += 1

            # Generar borradores de publicación para YouTube y TikTok
            draft_title = var.get("title") or f"Clip {created_renders}"
            draft_desc = var.get("description")
            tags = var.get("tags") or []
            drafts = meta_gen.generate_drafts(
                render_id=render.id,
                base_title=draft_title,
                base_description=draft_desc,
                tags=tags,
                visibility_yt=settings.publishing.default_visibility.get("youtube", "private"),
                visibility_tt=settings.publishing.default_visibility.get("tiktok", "SELF_ONLY"),
            )
            for d in drafts:
                db.add(d)
                db.flush()

                # Comprobar política de aprobación automática si está activa
                can_auto, reason = approval_svc.check_auto_approval_policy(render, d)
                if can_auto:
                    approval_svc.approve_draft_manually(d.id, actor="auto_policy")
                    print(f"  [Auto-Aprobado] Borrador {d.platform} encolado automáticamente ({reason})")

        project.workflow_status = "READY_TO_REVIEW"
        db.commit()

        print(f"\n✓ Proceso completado exitosamente.")
        print(f"  Proyecto ID: {project.id}")
        print(f"  Variantes renderizadas: {created_renders}")
        print(f"  Consulta los clips con: clipflow clips {project.id}")
        return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Lista todos los proyectos registrados."""
    init_db()
    with get_db_context() as db:
        projects = db.query(Project).order_by(Project.created_at.desc()).all()
        if not projects:
            print("No hay proyectos registrados.")
            return 0

        print(f"{'ID PROYECTO':38} {'ESTADO':15} {'RENDERS':8} {'CREADO':19} FUENTE")
        print("-" * 100)
        for p in projects:
            src = p.source.canonical_source if p.source else "N/A"
            dt_str = p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "-"
            print(f"{p.id:38} {p.workflow_status:15} {len(p.renders):<8} {dt_str:19} {src}")
        return 0


def cmd_clips(args: argparse.Namespace) -> int:
    """Muestra los clips renderizados y borradores de un proyecto."""
    init_db()
    with get_db_context() as db:
        project = db.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Proyecto no encontrado: {args.project_id}", file=sys.stderr)
            return 2

        print(f"Clips del proyecto {project.id} ({project.workflow_status}):")
        if not project.renders:
            print("  No hay renders completados en este proyecto.")
            return 0

        for idx, r in enumerate(project.renders, 1):
            print(f"\n[{idx}] Render ID: {r.id}")
            print(f"    Plataforma objetivo: {r.target_platform} | Duración: {r.duration_sec:.1f}s | Dimensiones: {r.width}x{r.height}")
            print(f"    Estado QA: {r.qa_status} | Archivo: {r.video_path}")
            if r.drafts:
                print("    Borradores de publicación:")
                for d in r.drafts:
                    job_status = d.publish_jobs[-1].status if d.publish_jobs else "Sin encolar"
                    print(f"      ● [{d.platform.upper()}] ID: {d.id} | Visibilidad: {d.visibility} | Estado: {job_status}")
                    print(f"        Título: {d.title}")
        return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Aprueba un borrador y lo coloca en la cola de publicación."""
    init_db()
    with get_db_context() as db:
        approval_svc = ApprovalService(db)
        # Buscar por ID de draft directo o por render_id + plataforma
        draft = db.query(PostDraft).filter(PostDraft.id == args.target_id).first()
        if not draft:
            # Intentar buscar por render_id
            draft = (
                db.query(PostDraft)
                .filter(PostDraft.render_id == args.target_id, PostDraft.platform == args.platform)
                .first()
            )

        if not draft:
            print(f"No se encontró borrador para el ID {args.target_id} en la plataforma {args.platform}", file=sys.stderr)
            return 2

        job = approval_svc.approve_draft_manually(draft.id, actor="cli_user")
        print(f"✓ Borrador aprobado para {draft.platform.upper()} (ID: {draft.id})")
        print(f"  Trabajo en cola ID: {job.id}")
        print(f"  Idempotency Key: {job.idempotency_key}")
        return 0


def cmd_publish(args: argparse.Namespace) -> int:
    """
    Ejecuta el ciclo de publicación para los clips aprobados de un proyecto.
    Respeta el modo de publicación y no publica contenido público sin autorización.
    """
    init_db()
    publisher = UploadPostPublisher(mock_mode=args.mode == "mock")
    worker = PublishQueueWorker(get_db_context, publisher)

    print(f"Iniciando despacho de publicación (Modo: {args.mode})...")
    with get_db_context() as db:
        project = db.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Proyecto no encontrado: {args.project_id}", file=sys.stderr)
            return 2

    # Procesar trabajos en cola
    worker.run_loop(once=True)
    print("Despacho completado. Consulta el estado con: clipflow status " + args.project_id)
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    """Programa la publicación de un proyecto o clip en una fecha/hora específica."""
    init_db()
    try:
        scheduled_utc = parse_schedule_to_utc(args.date)
    except Exception as e:
        print(f"Error interpretando fecha: {e}", file=sys.stderr)
        return 2

    print(f"Programando publicación para fecha UTC: {scheduled_utc.strftime('%Y-%m-%d %H:%M:%S UTC')}")
    with get_db_context() as db:
        project = db.query(Project).filter(Project.id == args.project_id).first()
        if not project:
            print(f"Proyecto no encontrado: {args.project_id}", file=sys.stderr)
            return 2

        scheduled_count = 0
        approval_svc = ApprovalService(db)
        for r in project.renders:
            for d in r.drafts:
                approval_svc.approve_draft_manually(d.id, actor="cli_scheduler", scheduled_at_utc=scheduled_utc)
                scheduled_count += 1

        print(f"✓ {scheduled_count} publicaciones programadas con éxito.")
        return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Muestra el estado detallado de un proyecto y sus publicaciones remotas."""
    init_db()
    with get_db_context() as db:
        reporting_svc = ReportingService(db)
        try:
            summary = reporting_svc.get_project_summary(args.project_id)
            print(json.dumps(summary, indent=2, ensure_ascii=False))
            return 0
        except Exception as e:
            print(f"Error consultando estado: {e}", file=sys.stderr)
            return 2


def cmd_worker(args: argparse.Namespace) -> int:
    """Inicia el worker continuo de la cola de publicación usando UnifiedPublisher + credenciales cifradas."""
    from clipflow.publishing.unified import UnifiedPublisher

    init_db()
    if args.mock:
        publisher = UnifiedPublisher(mock_mode=True)
    else:
        # PublishQueueWorker inyecta ConnectionService(session) dinámicamente en cada ciclo
        publisher = None
    worker = PublishQueueWorker(get_db_context, publisher)
    print("Iniciando worker persistente de ClipFlow (Presiona Ctrl+C para detener)...")
    worker.run_loop(poll_interval_sec=args.interval, once=args.once)
    return 0


def cmd_live_peak_worker(args: argparse.Namespace) -> int:
    """Ejecuta el monitor continuo 24/7 de Live Peak Intelligence cuando está habilitado."""
    import time
    from clipflow.live_peak.service import LivePeakService

    init_db()
    print("Iniciando worker Live Peak Intelligence 24/7 (Presiona Ctrl+C para detener)...")
    try:
        while True:
            with get_db_context() as db:
                res = LivePeakService(db).run_worker_tick()
            if args.once:
                print(json.dumps(res, ensure_ascii=False))
                break
            time.sleep(max(1.0, float(args.interval)))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Deteniendo live-peak-worker limpiamente...")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Genera informe de proyecto en formato JSON o texto plano."""
    return cmd_status(args)


def cmd_web(args: argparse.Namespace) -> int:
    """Inicia el panel de administración web."""
    import uvicorn
    print(f"Iniciando panel de administración en http://{args.host}:{args.port}")
    uvicorn.run("clipflow.web.app:app", host=args.host, port=args.port, reload=False)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipflow",
        description="ClipFlow: Orquestador y publicador automatizado de clips para YouTube Shorts y TikTok.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # doctor
    sub.add_parser("doctor", help="Comprueba el entorno, dependencias y conectividad.")

    # accounts
    sub.add_parser("accounts", help="Lista perfiles y cuentas conectadas en Upload-Post.")

    # create
    p_create = sub.add_parser("create", help="Crea un proyecto e inicia la generación y validación de clips.")
    p_create.add_argument("--source", required=True, help="Ruta de video local o URL HTTPS compatible.")
    p_create.add_argument("--srt", help="Ruta opcional a archivo de subtítulos .srt verificado.")
    p_create.add_argument("--platform", action="append", choices=["youtube_shorts", "tiktok"], help="Plataformas objetivo.")
    p_create.add_argument("--max-clips", type=int, default=10, help="Número máximo de clips a generar (hasta 10).")
    p_create.add_argument("--portrait-style", default="podcast", help="auto | interview | podcast.")
    p_create.add_argument("--template", default="classic", help="classic | editorial | street | news | cinema.")

    # list
    sub.add_parser("list", help="Lista todos los proyectos registrados.")

    # clips
    p_clips = sub.add_parser("clips", help="Muestra los clips renderizados y borradores de un proyecto.")
    p_clips.add_argument("project_id", help="ID del proyecto.")

    # approve
    p_app = sub.add_parser("approve", help="Aprueba un borrador y lo encola para publicación.")
    p_app.add_argument("target_id", help="ID del borrador o del render.")
    p_app.add_argument("--platform", choices=["youtube", "tiktok"], default="youtube", help="Plataforma a publicar.")

    # publish
    p_pub = sub.add_parser("publish", help="Despacha los clips aprobados de un proyecto.")
    p_pub.add_argument("project_id", help="ID del proyecto.")
    p_pub.add_argument("--mode", default="private-test", choices=["private-test", "mock", "manual"], help="Modo de publicación.")

    # schedule
    p_sch = sub.add_parser("schedule", help="Programa la publicación de un proyecto en una fecha/hora.")
    p_sch.add_argument("project_id", help="ID del proyecto.")
    p_sch.add_argument("--date", required=True, help="Fecha ISO (ej: 2026-12-03T09:00:00-04:00).")

    # status
    p_stat = sub.add_parser("status", help="Muestra el estado completo de un proyecto.")
    p_stat.add_argument("project_id", help="ID del proyecto.")

    # worker
    p_work = sub.add_parser("worker", help="Ejecuta el worker de cola y reconciliación.")
    p_work.add_argument("--interval", type=float, default=5.0, help="Intervalo de sondeo en segundos.")
    p_work.add_argument("--once", action="store_true", help="Procesa un solo ciclo y finaliza.")
    p_work.add_argument("--mock", action="store_true", help="Ejecuta en modo simulado sin credenciales.")

    # live-peak-worker
    p_lp = sub.add_parser("live-peak-worker", help="Ejecuta el monitor Live Peak Intelligence 24/7 (opt-in).")
    p_lp.add_argument("--interval", type=float, default=5.0, help="Intervalo de muestreo en segundos.")
    p_lp.add_argument("--once", action="store_true", help="Procesa un solo ciclo y finaliza.")

    # report
    p_rep = sub.add_parser("report", help="Genera reporte en JSON.")
    p_rep.add_argument("project_id", help="ID del proyecto.")
    p_rep.add_argument("--format", default="json", choices=["json"])

    # web
    p_web = sub.add_parser("web", help="Inicia el panel web de administración.")
    p_web.add_argument("--host", default="127.0.0.1")
    p_web.add_argument("--port", type=int, default=8000)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    setup_cli_logging()

    handlers = {
        "doctor": cmd_doctor,
        "accounts": cmd_accounts,
        "create": cmd_create,
        "list": cmd_list,
        "clips": cmd_clips,
        "approve": cmd_approve,
        "publish": cmd_publish,
        "schedule": cmd_schedule,
        "status": cmd_status,
        "worker": cmd_worker,
        "live-peak-worker": cmd_live_peak_worker,
        "report": cmd_report,
        "web": cmd_web,
    }

    handler = handlers.get(args.command)
    if not handler:
        parser.print_help()
        return 2

    try:
        return handler(args)
    except Exception as e:
        logger.exception("Error durante la ejecución del comando %s", args.command)
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
