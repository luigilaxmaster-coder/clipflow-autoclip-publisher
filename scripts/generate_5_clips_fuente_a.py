"""Genera 5 clips verticales 9:16 de Fuente A con subtítulos quemados usando el motor de AutoClip."""
from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from backend.services.studio.models import Draft, Scene
from backend.services.studio.render import render_draft
from backend.services.studio.store import directory
from backend.core.database import SessionLocal, create_tables
from backend.models.project import Project
from clipflow.media_qa import MediaQA

root_dir = Path(__file__).resolve().parent.parent
dest_dir = root_dir / "clips_para_seleccion" / "FuenteA_LibroEnoc_Saturno"
dest_dir.mkdir(parents=True, exist_ok=True)

project_id = "youtube-NbDzVqBhceY"
proj_dir = directory(project_id)
raw_dir = proj_dir / "raw"
raw_dir.mkdir(parents=True, exist_ok=True)

src_video = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "NbDzVqBhceY" / "input.mp4"
src_srt = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "NbDzVqBhceY" / "input.es.srt"

target_video = raw_dir / "input.mp4"
target_srt = raw_dir / "input.srt"

if not target_video.exists():
    shutil.copy2(src_video, target_video)
if not target_srt.exists():
    shutil.copy2(src_srt, target_srt)

create_tables()
with SessionLocal() as db:
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        p = Project(id=project_id, name="El Libro de Enoc - Saturno", project_type="default", video_path=str(target_video))
        db.add(p)
    else:
        p.video_path = str(target_video)
    db.commit()

# Copiar clip 1 ya renderizado si existe
existing_clip1 = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "NbDzVqBhceY" / "clip_1080x1920_48s.mp4"
clip1_dest = dest_dir / "clip_01_saturno_prision_enoc.mp4"
if existing_clip1.exists() and not clip1_dest.exists():
    shutil.copy2(existing_clip1, clip1_dest)
    print(f"✓ Clip 1 reutilizado: {clip1_dest.name}")

# Definir los otros 4 clips
clips_to_generate = [
    {
        "filename": "clip_02_saturnales_romanas.mp4",
        "title": "Las Saturnales de Roma y el Dios Encadenado",
        "start": 48.0,
        "end": 95.0,
        "label": "Las Saturnales romanas",
    },
    {
        "filename": "clip_03_rito_liberacion_edad_oro.mp4",
        "title": "El Rito de Liberación y la Antigua Edad de Oro",
        "start": 120.0,
        "end": 168.0,
        "label": "Rito de liberación de Saturno",
    },
    {
        "filename": "clip_04_mito_griego_cronos.mp4",
        "title": "El Mito Oscuro de Cronos y el Encarcelamiento",
        "start": 180.0,
        "end": 225.0,
        "label": "El mito de Cronos y su destino",
    },
    {
        "filename": "clip_05_hexagono_polar_senales.mp4",
        "title": "El Enigma Cósmico de Saturno: Ataduras y Destino",
        "start": 245.0,
        "end": 290.0,
        "label": "El enigma cósmico de Saturno",
    },
]

qa = MediaQA()

for item in clips_to_generate:
    out_file = dest_dir / item["filename"]
    if out_file.exists():
        print(f"✓ Ya existe: {item['filename']}")
        continue

    print(f"\nRenderizando {item['filename']} ({item['start']}s - {item['end']}s)...")
    job_id = f"render-{uuid.uuid4().hex[:8]}"
    draft = Draft(
        id=f"draft-{job_id}",
        title=item["title"],
        hook="",
        aspect="portrait",
        layout="blur",
        subtitles=True,
        original_audio=True,
        scenes=[
            Scene(
                id=str(uuid.uuid4()),
                label=item["label"],
                start=item["start"],
                end=item["end"],
                evidence=item["label"],
            )
        ],
    )

    res = render_draft(project_id, target_video, draft, job_id, lambda p: None)
    rendered_mp4 = proj_dir / "output" / "studio" / f"{job_id}.mp4"
    if rendered_mp4.exists():
        shutil.copy2(rendered_mp4, out_file)
        rep = qa.validate_clip(out_file, platform="youtube_shorts", max_duration_sec=60.0)
        print(f"  ✓ Generado con éxito: {item['filename']} ({out_file.stat().st_size / (1024*1024):.2f} MB, {rep.duration_sec:.1f}s, QA: {rep.overall_status})")
    else:
        print(f"  ✗ Error al generar {item['filename']}")

print("\n=== Todos los clips de Fuente A completados ===")

