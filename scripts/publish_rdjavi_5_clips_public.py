"""Publica los 5 clips de Fuente C (RDjavi - Directo GTA 6) en YouTube en modo PUBLIC.
Reglas:
- Visibilidad: public
- Sin emojis en los títulos
- Hashtags optimizados para Shorts, GTA 6 y Gaming
- Safe Area y QA previo verificado
- Idempotencia y registro persistente en SQLite y JSON
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env")

from clipflow.db import get_db_context, init_db
from clipflow.media_qa import MediaQA
from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.publishing.upload_post import UploadPostPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("publish_rdjavi_public")
sys.stdout.reconfigure(line_buffering=True)


RDJAVI_POSTS = [
    {
        "clip_name": "clip_01_rdjavi_conteo_nervios.mp4",
        "variant_id": "rdjavi-clip-01",
        "title": "Conteo regresivo y nervios antes de abrir GTA 6 en directo #Shorts #GTA6 #RDjavi",
        "description": "Momento de pura tension y nervios en directo contando los ultimos segundos antes de iniciar GTA 6. #GTA6 #RDjavi #Gaming #Shorts #Directo #Gameplay",
    },
    {
        "clip_name": "clip_02_rdjavi_menu_clasico_og.mp4",
        "variant_id": "rdjavi-clip-02",
        "title": "El diseno del menu de GTA 6 parece un GTA clasico #Shorts #GTA6 #Detalles",
        "description": "Analisis en vivo de los menus, interfaz y opciones graficas de GTA 6 con estilo retro y clasico. #GTA6 #Detalles #Gaming #Shorts #Analisis #RockstarGames",
    },
    {
        "clip_name": "clip_03_rdjavi_ajustes_secretos_gta6.mp4",
        "variant_id": "rdjavi-clip-03",
        "title": "Los primeros ajustes y opciones graficas reveladas en GTA 6 #Shorts #GTA6 #Ajustes",
        "description": "Explorando las opciones de configuracion, HDR y graficos de GTA 6 por primera vez en vivo. #GTA6 #Configuracion #Shorts #Gaming #GTA6News",
    },
    {
        "clip_name": "clip_04_rdjavi_historia_jason_lucia.mp4",
        "variant_id": "rdjavi-clip-04",
        "title": "Como se conocieron Jason y Lucia en GTA 6 revelado #Shorts #GTA6 #Historia",
        "description": "El origen de la historia de amor y crimen entre Jason y Lucia en la introduccion de GTA 6. #GTA6 #Historia #Lucia #Jason #Shorts #Rockstar",
    },
    {
        "clip_name": "clip_05_rdjavi_idioma_espanol.mp4",
        "variant_id": "rdjavi-clip-05",
        "title": "Configurando el idioma espanol en GTA 6 por primera vez #Shorts #GTA6 #Espanol",
        "description": "Descubriendo las opciones de idioma y subtitulos en espanol dentro del menu oficial de GTA 6. #GTA6 #Espanol #GTA #Shorts #Videojuegos",
    },
]


def publish_rdjavi():
    print("=== Iniciando Publicación Pública de 5 Clips de RDjavi ===")
    init_db()

    qa = MediaQA()
    publisher = UploadPostPublisher(mock_mode=False)

    if not publisher.is_configured:
        print("ERROR: UPLOAD_POST_API_KEY no configurada.", file=sys.stderr)
        sys.exit(1)

    profiles = publisher.list_profiles()
    target_profile = publisher.user or "default"
    prof_data = next((p for p in profiles if p.get("username") == target_profile), profiles[0] if profiles else None)
    if not prof_data or "youtube" not in [c.lower() for c in prof_data.get("connected_platforms", [])]:
        print(f"ERROR: El perfil '{target_profile}' no tiene YouTube conectado.", file=sys.stderr)
        sys.exit(1)

    target_profile = prof_data.get("username", "default")
    print(f"Perfil seleccionado: {target_profile} (YouTube conectado)")

    source_url = "https://www.youtube.com/watch?v=dmd5f5knGrw"
    base_dir = root_dir / "clips_para_seleccion" / "FuenteC_RDjavi_GTA6"
    receipts_dir = root_dir / "runs" / "real-two-sources-2026-11-19" / "publish_receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)

    results = []

    for item in RDJAVI_POSTS:
        clip_path = base_dir / item["clip_name"]

        if not clip_path.exists() or clip_path.stat().st_size == 0:
            print(f"ERROR: No existe el clip {clip_path}", file=sys.stderr)
            continue

        qa_rep = qa.validate_clip(clip_path, platform="youtube_shorts", max_duration_sec=60.0)
        if qa_rep.overall_status != "PASSED":
            print(f"QA FAILED para {item['clip_name']}: {qa_rep.reasons}", file=sys.stderr)
            continue

        clip_sha256 = hashlib.sha256(clip_path.read_bytes()).hexdigest()
        idempotency_key = f"pub-yt-rdjavi-{item['variant_id']}-{clip_sha256[:10]}"

        print(f"\n--- Preparando: {item['clip_name']} ---")
        print(f"  ● Titulo: {item['title']} (len: {len(item['title'])})")
        print(f"  ● Visibilidad: PUBLIC")
        print(f"  ● Idempotency Key: {idempotency_key}")

        job_already_submitted = False
        existing_remote_url = None
        existing_remote_req_id = None

        with get_db_context() as db:
            source = db.query(Source).filter(Source.canonical_source == source_url).first()
            if not source:
                source = Source(
                    source_kind="url",
                    canonical_source=source_url,
                    local_path=str(root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "dmd5f5knGrw" / "section_8m_14m.mp4"),
                    duration_sec=360.0,
                    rights_confirmed=True,
                )
                db.add(source)
                db.flush()

            project = db.query(Project).filter(Project.autoclip_project_id == "youtube-dmd5f5knGrw").first()
            if not project:
                project = Project(
                    source_id=source.id,
                    autoclip_project_id="youtube-dmd5f5knGrw",
                    upstream_version="1.5.8",
                    workflow_status="CLIPS_GENERATED",
                )
                db.add(project)
                db.flush()

            render = db.query(Render).filter(Render.video_sha256 == clip_sha256).first()
            if not render:
                render = Render(
                    project_id=project.id,
                    output_variant_id=item["variant_id"],
                    studio_job_id=item["variant_id"],
                    target_platform="youtube_shorts",
                    video_path=str(clip_path),
                    video_sha256=clip_sha256,
                    width=qa_rep.width,
                    height=qa_rep.height,
                    duration_sec=qa_rep.duration_sec,
                    qa_status="PASSED",
                    qa_json=json.dumps({"status": "PASSED"}),
                )
                db.add(render)
                db.flush()

            draft = db.query(PostDraft).filter(PostDraft.render_id == render.id, PostDraft.platform == "youtube").first()
            if not draft:
                draft = PostDraft(
                    render_id=render.id,
                    platform="youtube",
                    account_id=target_profile,
                    title=item["title"],
                    description=item["description"],
                    visibility="public",
                    approved_at=utc_now(),
                    approval_actor="user_command",
                )
                db.add(draft)
                db.flush()
            else:
                draft.visibility = "public"
                draft.title = item["title"]
                draft.description = item["description"]

            job = db.query(PublishJob).filter(PublishJob.idempotency_key == idempotency_key).first()
            if not job:
                job = PublishJob(
                    post_draft_id=draft.id,
                    idempotency_key=idempotency_key,
                    status="UPLOADING",
                    attempt_count=1,
                )
                db.add(job)
                db.flush()
            else:
                if job.remote_request_id and job.status in ("UPLOADING", "COMPLETED", "PUBLISHED"):
                    job_already_submitted = True
                job.attempt_count += 1

            existing_remote_url = job.remote_url
            existing_remote_req_id = job.remote_request_id
            db.commit()
            job_db_id = job.id

        if existing_remote_url:
            print(f"  ✓ Video ya publicado previamente con éxito! URL: {existing_remote_url}")
            results.append({
                "variant_id": item["variant_id"],
                "clip_name": item["clip_name"],
                "title": item["title"],
                "visibility": "public",
                "status": "COMPLETED",
                "post_url": existing_remote_url,
                "request_id": existing_remote_req_id,
            })
            continue

        req_id_to_poll = existing_remote_req_id
        if not job_already_submitted or not req_id_to_poll:
            print(f"  -> Enviando a Upload-Post...")
            submit_res = publisher.submit_post(
                video_path=str(clip_path),
                title=item["title"],
                platform="youtube",
                profile=target_profile,
                description=item["description"],
                visibility="public",
                idempotency_key=idempotency_key,
            )

            receipt_file = receipts_dir / f"request_{item['variant_id']}.json"
            req_info = {
                "variant_id": item["variant_id"],
                "clip_name": item["clip_name"],
                "title": item["title"],
                "visibility": "public",
                "idempotency_key": idempotency_key,
                "ok": submit_res.ok,
                "request_id": submit_res.request_id,
                "job_id": submit_res.job_id,
                "error": submit_res.error,
                "timestamp": utc_now().isoformat(),
            }
            receipt_file.write_text(json.dumps(req_info, indent=2, ensure_ascii=False), encoding="utf-8")

            if not submit_res.ok:
                print(f"  ✗ Upload-Post rechazó la solicitud: {submit_res.error}")
                with get_db_context() as db:
                    j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                    if j:
                        j.status = "FAILED"
                        j.last_error = submit_res.error
                        db.commit()
                results.append({"clip": item["clip_name"], "status": "FAILED", "error": submit_res.error})
                continue

            req_id_to_poll = submit_res.request_id
            print(f"  ✓ Solicitud aceptada. Request ID: {req_id_to_poll}")
            with get_db_context() as db:
                j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                if j:
                    j.remote_request_id = submit_res.request_id
                    j.remote_job_id = submit_res.job_id
                    db.commit()
        else:
            print(f"  ℹ Video ya enviado previamente con Request ID: {req_id_to_poll}")

        # Polling del estado
        print(f"  -> Consultando estado de procesamiento (Req ID: {req_id_to_poll})...")
        final_status = "UNKNOWN"
        post_url = None
        for poll_idx in range(1, 35):
            time.sleep(8)
            status_res = publisher.get_status(req_id_to_poll)
            print(f"     Poll {poll_idx}/35: status={status_res.status}, post_url={status_res.remote_url}")
            if status_res.status in ("PUBLISHED", "completed", "successful"):
                final_status = "COMPLETED"
                post_url = status_res.remote_url
                print(f"     ✓ Publicación COMPLETADA con éxito! URL: {post_url}")
                with get_db_context() as db:
                    j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                    if j:
                        j.status = "COMPLETED"
                        j.remote_url = post_url
                        db.commit()
                break
            elif status_res.status in ("failed", "rejected", "FAILED"):
                final_status = "FAILED"
                print(f"     ✗ Publicación FALLIDA: {status_res.error}")
                with get_db_context() as db:
                    j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                    if j:
                        j.status = "FAILED"
                        j.last_error = status_res.error
                        db.commit()
                break

        res_receipt = receipts_dir / f"result_{item['variant_id']}.json"
        res_info = {
            "variant_id": item["variant_id"],
            "clip_name": item["clip_name"],
            "title": item["title"],
            "visibility": "public",
            "status": final_status,
            "post_url": post_url,
            "request_id": req_id_to_poll,
            "timestamp": utc_now().isoformat(),
        }
        res_receipt.write_text(json.dumps(res_info, indent=2, ensure_ascii=False), encoding="utf-8")
        results.append(res_info)
        time.sleep(3)  # Pausa entre subidas

    print("\n=== Resumen de Publicación RDjavi ===")
    for r in results:
        print(f"  ● {r.get('clip_name')}: {r.get('status')} -> {r.get('post_url') or r.get('error') or 'En cola'}")


if __name__ == "__main__":
    publish_rdjavi()
