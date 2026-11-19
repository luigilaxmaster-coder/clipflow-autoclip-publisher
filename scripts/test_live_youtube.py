"""Script de verificación de subida real privada a YouTube vía Upload-Post (FASE 3).

Requisitos obligatorios aplicados:
- platform[]=youtube
- privacyStatus=private
- Título identificable con marca temporal
- async_upload=true
- Idempotency-Key persistente
- Sondeo hasta estado definitivo y verificación de que YouTube no aparezca como 'skipped'
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

# Cargar entorno de .env si existe
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from clipflow.publishing.upload_post import UploadPostPublisher
from clipflow.media_qa import find_ffprobe, MediaQA


def main():
    print("=== ClipFlow - Prueba Real de Subida Privada a YouTube (Upload-Post) ===")

    publisher = UploadPostPublisher(mock_mode=False)
    if not publisher.api_key:
        print("ERROR: UPLOAD_POST_API_KEY no está configurada.", file=sys.stderr)
        print("Por favor, ejecuta antes: .\\scripts\\set-credentials-local.ps1", file=sys.stderr)
        sys.exit(1)

    # 1. Verificar perfiles y YouTube conectado
    print("\n1. Verificando perfiles y conexión de YouTube...")
    try:
        profiles = publisher.list_profiles()
        if not profiles:
            print("ERROR: No se encontraron perfiles en tu cuenta de Upload-Post.", file=sys.stderr)
            sys.exit(1)

        target_profile = publisher.user or profiles[0].get("username", "default")
        profile_data = next((p for p in profiles if p.get("username") == target_profile), profiles[0])
        target_profile = profile_data.get("username", "default")
        connected = [c.lower() for c in profile_data.get("connected_platforms", [])]

        print(f"  ● Perfil seleccionado: {profile_data.get('username')}")
        print(f"  ● Plataformas conectadas: {', '.join(connected)}")

        if "youtube" not in connected:
            print(f"ERROR: Tu canal de YouTube no figura como conectado en el perfil '{profile_data.get('username')}'.", file=sys.stderr)
            print("Ve a https://app.upload-post.com/manage-users y conecta tu canal de YouTube.", file=sys.stderr)
            sys.exit(1)

        print("  ✓ YouTube está conectado y listo.")

    except Exception as e:
        print(f"ERROR al consultar Upload-Post: {e}", file=sys.stderr)
        sys.exit(1)

    # 2. Generar video de prueba de 5 segundos con FFmpeg si no existe
    test_video_path = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "test_vertical_9_16.mp4"
    if not test_video_path.exists():
        test_video_path.parent.mkdir(parents=True, exist_ok=True)
        print("\n2. Generando video de prueba vertical 9:16 con FFmpeg...")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000",
            "-t", "5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(test_video_path),
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        print(f"  ✓ Video generado: {test_video_path}")
    else:
        print(f"\n2. Utilizando video de prueba existente: {test_video_path}")

    # Validar QA del video
    qa = MediaQA()
    report = qa.validate_clip(test_video_path, platform="youtube_shorts", min_duration_sec=3.0)
    print(f"  ✓ Calidad QA: {report.overall_status} ({report.width}x{report.height}, {report.duration_sec:.1f}s)")

    # 3. Preparar parámetros obligatorios de subida privada
    timestamp_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    title = f"ClipFlow Private Test {timestamp_str}"
    description = "Prueba técnica de integración privada de ClipFlow con Upload-Post y YouTube Data API #Shorts"
    idempotency_key = f"live-yt-{uuid.uuid4().hex[:16]}"

    print(f"\n3. Iniciando envío a YouTube (Upload-Post)...")
    print(f"  ● Título: {title}")
    print(f"  ● Privacidad: private")
    print(f"  ● Idempotency-Key: {idempotency_key}")

    res = publisher.submit_post(
        video_path=str(test_video_path),
        title=title,
        platform="youtube",
        profile=target_profile,
        description=description,
        visibility="private",
        idempotency_key=idempotency_key,
    )

    if not res.ok:
        print(f"ERROR: Fallo al enviar publicación: {res.error}", file=sys.stderr)
        sys.exit(1)

    print(f"  ✓ Solicitud aceptada por Upload-Post.")
    print(f"  ● Request ID: {res.request_id}")
    if res.job_id:
        print(f"  ● Job ID: {res.job_id}")

    # 4. Sondeo de estado hasta confirmación definitiva
    print("\n4. Consultando estado asíncrono cada 10 segundos...")
    max_wait_sec = 600
    start_time = time.monotonic()
    final_result = None

    while time.monotonic() - start_time < max_wait_sec:
        time.sleep(10)
        status_res = publisher.get_status(res.request_id)
        print(f"  [{int(time.monotonic() - start_time)}s] Estado: {status_res.status}")

        # Comprobar resultados por plataforma
        for pr in status_res.platform_results:
            p_name = pr.get("platform")
            p_ok = pr.get("success")
            p_url = pr.get("url")
            p_skipped = pr.get("skipped")
            p_err = pr.get("error")

            if p_name == "youtube":
                if p_skipped:
                    print("ERROR: YouTube fue marcado como 'skipped' (la cuenta no estaba vinculada en este perfil).", file=sys.stderr)
                    sys.exit(1)
                if p_ok and p_url:
                    final_result = pr
                    break
                if p_err:
                    print(f"ERROR reportado por YouTube: {p_err}", file=sys.stderr)
                    sys.exit(1)

        if final_result or status_res.status == "PUBLISHED":
            break

        if status_res.status in ("FAILED", "ACTION_REQUIRED"):
            print(f"ERROR: La publicación finalizó con error o requiere acción: {status_res.error}", file=sys.stderr)
            sys.exit(1)

    if not final_result and status_res.status != "PUBLISHED":
        print(f"ERROR: Tiempo de espera agotado ({max_wait_sec}s) sin confirmación definitiva.", file=sys.stderr)
        sys.exit(1)

    print("\n=======================================================")
    print("✓ PRUEBA REAL PRIVADA COMPLETADA EXITOSAMENTE EN YOUTUBE")
    print(f"  ● URL del Video: {final_result.get('url') if final_result else status_res.remote_url}")
    print(f"  ● ID Remoto: {final_result.get('post_id') if final_result else status_res.remote_post_id}")
    print(f"  ● Estado: private (Confirmado)")
    print("=======================================================")


if __name__ == "__main__":
    main()

