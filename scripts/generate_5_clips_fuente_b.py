"""Genera 5 clips verticales 9:16 de Fuente B (Directo TheGrefg) en clips_para_seleccion."""
from __future__ import annotations

import subprocess
import time
from pathlib import Path
from clipflow.media_qa import MediaQA

root_dir = Path(__file__).resolve().parent.parent
dest_dir = root_dir / "clips_para_seleccion" / "FuenteB_Directo_TheGrefg"
dest_dir.mkdir(parents=True, exist_ok=True)

source_video = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "kE-21fcphn8" / "live_240s.mp4"

# Si aún no ha terminado, esperar brevemente
for _ in range(60):
    if source_video.exists() and source_video.stat().st_size > 10 * 1024 * 1024:
        break
    time.sleep(5)

if not source_video.exists():
    # Fallback al test_live.mp4 si fuera necesario
    source_video = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "kE-21fcphn8" / "test_live.mp4"

print(f"Usando video fuente de directo: {source_video.name} ({source_video.stat().st_size / (1024*1024):.2f} MB)")

clips_config = [
    {
        "filename": "clip_01_thegrefg_directo_jugando_gta6.mp4",
        "title": "TheGrefg - Jugando GTA 6 en Directo",
        "start": 5.0,
        "duration": 40.0,
    },
    {
        "filename": "clip_02_thegrefg_directo_reaccion_chat.mp4",
        "title": "TheGrefg - Reacción y Comentarios en Vivo",
        "start": 50.0,
        "duration": 40.0,
    },
    {
        "filename": "clip_03_thegrefg_directo_gameplay_mision.mp4",
        "title": "TheGrefg - Gameplay y Acción en el Directo",
        "start": 95.0,
        "duration": 40.0,
    },
    {
        "filename": "clip_04_thegrefg_directo_momento_epico.mp4",
        "title": "TheGrefg - Momento Épico en GTA 6",
        "start": 140.0,
        "duration": 40.0,
    },
    {
        "filename": "clip_05_thegrefg_directo_meta_20m_subs.mp4",
        "title": "TheGrefg - Camino a los 20 Millones de Suscriptores",
        "start": 185.0,
        "duration": 40.0,
    },
]

qa = MediaQA()

for item in clips_config:
    out_file = dest_dir / item["filename"]
    if out_file.exists():
        print(f"✓ Ya existe: {item['filename']}")
        continue

    print(f"\nRenderizando {item['filename']} (Inicio: {item['start']}s, Duración: {item['duration']}s)...")

    # Filtro FFmpeg para formato vertical 9:16 (1080x1920) con fondo desenfocado (layout blur idéntico a AutoClip)
    # [0:v]split=2[bg][fg]; [bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=20:5[bg_blur]; [fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled]; [bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[outv]
    vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_blur];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled];"
        "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[outv]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(item["start"]),
        "-i", str(source_video),
        "-t", str(item["duration"]),
        "-filter_complex", vf,
        "-map", "[outv]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        str(out_file),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and out_file.exists():
        rep = qa.validate_clip(out_file, platform="youtube_shorts", max_duration_sec=60.0)
        print(f"  ✓ Generado con éxito: {item['filename']} ({out_file.stat().st_size / (1024*1024):.2f} MB, {rep.duration_sec:.1f}s, QA: {rep.overall_status})")
    else:
        print(f"  ✗ Error al generar {item['filename']}: {res.stderr[:300]}")

print("\n=== Todos los clips de Fuente B completados ===")

