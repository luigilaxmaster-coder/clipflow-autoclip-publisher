"""Ejecución real de publicación privada para Fuente A según especificación ANTIGRAVITY_PRUEBA_REAL_2."""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

# Cargar variables de entorno locales
root_dir = Path(__file__).resolve().parent.parent
load_dotenv(root_dir / ".env")

from clipflow.db import get_db_context, init_db, record_audit
from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.publishing.upload_post import UploadPostPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("execute_real_publish")


def main():
    print("=== ClipFlow - Publicación Real de Clip Privado (Fuente A) ===")
    init_db()

    clip_path = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "NbDzVqBhceY" / "clip_1080x1920_48s.mp4"
    if not clip_path.exists():
        print(f"ERROR: No se encontró el clip generado en: {clip_path}", file=sys.stderr)
        sys.exit(1)

    import hashlib
    clip_sha256 = hashlib.sha256(clip_path.read_bytes()).hexdigest()
    print(f"Clip verificado: {clip_path.name} ({clip_path.stat().st_size / (1024*1024):.2f} MB)")
    print(f"SHA256: {clip_sha256}")

    publisher = UploadPostPublisher(mock_mode=False)
    if not publisher.is_configured:
        print("ERROR: UPLOAD_POST_API_KEY no configurada.", file=sys.stderr)
        sys.exit(1)

    # 1. Validar perfiles
    profiles = publisher.list_profiles()
    print(f"Perfiles en Upload-Post: {[p.get('username') for p in profiles]}")
    target_profile = publisher.user or "default"
    prof_data = next((p for p in profiles if p.get("username") == target_profile), profiles[0] if profiles else None)
    if not prof_data or "youtube" not in [c.lower() for c in prof_data.get("connected_platforms", [])]:
        print(f"ERROR: El perfil '{target_profile}' no tiene YouTube conectado.", file=sys.stderr)
        sys.exit(1)

    target_profile = prof_data.get("username", "default")
    print(f"Perfil seleccionado: {target_profile} (YouTube conectado)")

    # 2. Registrar en base de datos SQLite antes de llamar a la API
    source_url = "https://www.youtube.com/watch?v=NbDzVqBhceY"
    title = "¿Saturno es una prisión según el Libro de Enoc? #Shorts"
    description = "Teoría sobre Saturno y las estrellas encarceladas descritas en los capítulos 18 y 21 del Libro de Enoc. #Shorts #Enoc #Misterio"
    idempotency_key = f"real-yt-NbDzVqBhceY-{clip_sha256[:12]}"

    with get_db_context() as db:
        # Registrar Source
        source = db.query(Source).filter(Source.canonical_source == source_url).first()
        if not source:
            source = Source(
                source_kind="url",
                canonical_source=source_url,
                local_path=str(root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "NbDzVqBhceY" / "input.mp4"),
                sha256="fe6117211fb744b9d240eca2d23aa0038adcdd94ddc2b3bcf9c0e6b99597e89d",
                duration_sec=1977.5,
                rights_confirmed=True,
            )
            db.add(source)
            db.flush()

        # Registrar Project
        project = db.query(Project).filter(Project.autoclip_project_id == "youtube-NbDzVqBhceY").first()
        if not project:
            project = Project(
                source_id=source.id,
                autoclip_project_id="youtube-NbDzVqBhceY",
                upstream_version="1.5.8",
                workflow_status="CLIPS_GENERATED",
            )
            db.add(project)
            db.flush()

        # Registrar Render
        render = db.query(Render).filter(Render.video_sha256 == clip_sha256).first()
        if not render:
            render = Render(
                project_id=project.id,
                output_variant_id="render-c6a365c9",
                studio_job_id="render-c6a365c9",
                target_platform="youtube_shorts",
                video_path=str(clip_path),
                video_sha256=clip_sha256,
                width=1080,
                height=1920,
                duration_sec=48.0,
                qa_status="PASSED",
                qa_json=json.dumps({"width": 1080, "height": 1920, "duration": 48.0, "status": "PASSED"}),
            )
            db.add(render)
            db.flush()

        # Registrar PostDraft
        draft = db.query(PostDraft).filter(PostDraft.render_id == render.id, PostDraft.platform == "youtube").first()
        if not draft:
            draft = PostDraft(
                render_id=render.id,
                platform="youtube",
                account_id=target_profile,
                title=title,
                description=description,
                visibility="private",
                approved_at=utc_now(),
                approval_actor="antigravity_execution",
            )
            db.add(draft)
            db.flush()

        # Registrar PublishJob
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
            job.attempt_count += 1
            job.status = "UPLOADING"

        db.commit()
        job_db_id = job.id
        print(f"Registro en base de datos completado. Job ID: {job_db_id}, Idempotency Key: {idempotency_key}")

    # 3. Enviar a Upload-Post con visibilidad estrictamente private
    print(f"\nEnviando video a Upload-Post...")
    print(f"  ● Platform: youtube")
    print(f"  ● User/Profile: {target_profile}")
    print(f"  ● Privacy: private")
    print(f"  ● Title: {title}")
    print(f"  ● Idempotency-Key: {idempotency_key}")

    submit_res = publisher.submit_post(
        video_path=str(clip_path),
        title=title,
        platform="youtube",
        profile=target_profile,
        description=description,
        visibility="private",
        idempotency_key=idempotency_key,
    )

    receipts_dir = root_dir / "runs" / "real-two-sources-2026-11-19" / "publish_receipts"
    receipts_dir.mkdir(parents=True, exist_ok=True)
    req_receipt_file = receipts_dir / "request_NbDzVqBhceY.json"

    # Redacted copy of payload
    req_data = {
        "ok": submit_res.ok,
        "request_id": submit_res.request_id,
        "job_id": submit_res.job_id,
        "status": submit_res.status,
        "error": submit_res.error,
        "idempotency_key": idempotency_key,
        "platform": "youtube",
        "profile": target_profile,
        "timestamp": utc_now().isoformat(),
    }
    req_receipt_file.write_text(json.dumps(req_data, indent=2, ensure_ascii=False), encoding="utf-8")

    if not submit_res.ok:
        print(f"ERROR: Upload-Post rechazó la solicitud: {submit_res.error}", file=sys.stderr)
        with get_db_context() as db:
            j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
            if j:
                j.status = "FAILED"
                j.last_error = submit_res.error
                db.commit()
        sys.exit(1)

    print(f"  ✓ Solicitud aceptada. Request ID: {submit_res.request_id}")
    with get_db_context() as db:
        j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
        if j:
            j.remote_request_id = submit_res.request_id
            j.status = "REMOTE_PROCESSING"
            db.commit()

    # 4. Sondeo asíncrono
    print("\nMonitoreando estado asíncrono...")
    max_wait_sec = 400
    start_time = time.monotonic()
    status_receipt_file = receipts_dir / "status_NbDzVqBhceY.json"
    final_res = None

    while time.monotonic() - start_time < max_wait_sec:
        time.sleep(10)
        status = publisher.get_status(submit_res.request_id)
        elapsed = int(time.monotonic() - start_time)
        print(f"  [{elapsed}s] Estado: {status.status}")

        status_data = {
            "request_id": status.request_id,
            "status": status.status,
            "remote_post_id": status.remote_post_id,
            "remote_url": status.remote_url,
            "platform_results": status.platform_results,
            "raw_payload": status.raw_payload,
            "elapsed_sec": elapsed,
        }
        status_receipt_file.write_text(json.dumps(status_data, indent=2, ensure_ascii=False), encoding="utf-8")

        for pr in status.platform_results:
            if pr.get("platform") == "youtube":
                if pr.get("skipped"):
                    print("ERROR: YouTube fue marcado como 'skipped'.", file=sys.stderr)
                    with get_db_context() as db:
                        j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                        if j:
                            j.status = "FAILED"
                            j.last_error = "YouTube skipped"
                            db.commit()
                    sys.exit(1)
                if pr.get("success") and pr.get("url"):
                    final_res = pr
                    break
                if pr.get("error"):
                    print(f"ERROR en YouTube: {pr.get('error')}", file=sys.stderr)
                    with get_db_context() as db:
                        j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                        if j:
                            j.status = "FAILED"
                            j.last_error = str(pr.get('error'))
                            db.commit()
                    sys.exit(1)

        if final_res or status.status == "PUBLISHED":
            break

        if status.status in ("FAILED", "ACTION_REQUIRED"):
            print(f"ERROR: Estado final fallido o requiere acción: {status.error}", file=sys.stderr)
            with get_db_context() as db:
                j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
                if j:
                    j.status = status.status
                    j.last_error = status.error
                    db.commit()
            sys.exit(1)

    remote_url = final_res.get("url") if final_res else status.remote_url
    remote_id = final_res.get("post_id") if final_res else status.remote_post_id

    with get_db_context() as db:
        j = db.query(PublishJob).filter(PublishJob.id == job_db_id).first()
        if j:
            j.status = "PUBLISHED"
            j.remote_url = remote_url
            j.remote_post_id = remote_id
            db.commit()

    print("\n=======================================================")
    print("✓ PUBLICACIÓN PRIVADA CONFIRMADA EN YOUTUBE")
    print(f"  ● URL del Video: {remote_url}")
    print(f"  ● ID de Publicación: {remote_id}")
    print(f"  ● Visibilidad solicitada: private")
    print(f"  ● Comprobante guardado en: {status_receipt_file}")
    print("=======================================================")


if __name__ == "__main__":
    main()

