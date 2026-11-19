"""Genera 5 clips verticales 9:16 de Fuente C (RDjavi Directo GTA 6).
Características:
- Formato vertical 9:16 (1080x1920) adaptado para YouTube Shorts
- Fondo desenfocado dinámico (layout blur)
- Subtítulos en español sincronizados y quemados con estilo de alto contraste
- Safe area para YouTube Shorts (MarginV=340 para evitar solapamiento de UI)
- Validación MediaQA
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from clipflow.media_qa import MediaQA

root_dir = Path(__file__).resolve().parent.parent
dest_dir = root_dir / "clips_para_seleccion" / "FuenteC_RDjavi_GTA6"
dest_dir.mkdir(parents=True, exist_ok=True)

source_video = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "dmd5f5knGrw" / "section_8m_14m.mp4"
source_srt = root_dir / "runs" / "real-two-sources-2026-11-19" / "media" / "dmd5f5knGrw" / "input.es.srt"

if not source_video.exists():
    print(f"ERROR: No se encontró el video fuente: {source_video}", file=sys.stderr)
    sys.exit(1)

print(f"Video fuente: {source_video.name} ({source_video.stat().st_size / (1024*1024):.2f} MB)")


def tc_to_sec(tc: str) -> float:
    parts = re.split(r"[:,\.]", tc)
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2]) + int(parts[3]) / 1000.0


def sec_to_tc(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    ms = int(round((sec - int(sec)) * 1000))
    if ms >= 1000:
        s += 1
        ms = 0
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# Cargar y parsear subtítulos globales
srt_content = source_srt.read_text(encoding="utf-8")
pattern = re.compile(
    r"(\d+)\n(\d{2}:\d{2}:\d{2}[,\.]\d{3}) --> (\d{2}:\d{2}:\d{2}[,\.]\d{3})\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)
all_cues = []
for _, s_str, e_str, text in pattern.findall(srt_content):
    all_cues.append({
        "start": tc_to_sec(s_str),
        "end": tc_to_sec(e_str),
        "text": "\n".join(line.strip() for line in text.strip().splitlines() if line.strip()),
    })

CLIPS_CONFIG = [
    {
        "filename": "clip_01_rdjavi_conteo_nervios.mp4",
        "title": "Conteo regresivo y nervios antes de abrir GTA 6 en directo #Shorts #GTA6 #RDjavi",
        "start": 6.0,
        "duration": 40.0,
    },
    {
        "filename": "clip_02_rdjavi_menu_clasico_og.mp4",
        "title": "El diseno del menu de GTA 6 parece un GTA clasico #Shorts #GTA6 #Detalles",
        "start": 77.0,
        "duration": 41.0,
    },
    {
        "filename": "clip_03_rdjavi_ajustes_secretos_gta6.mp4",
        "title": "Los primeros ajustes y opciones graficas reveladas en GTA 6 #Shorts #GTA6 #Ajustes",
        "start": 153.0,
        "duration": 45.0,
    },
    {
        "filename": "clip_04_rdjavi_historia_jason_lucia.mp4",
        "title": "Como se conocieron Jason y Lucia en GTA 6 revelado #Shorts #GTA6 #Historia",
        "start": 232.0,
        "duration": 28.0,
    },
    {
        "filename": "clip_05_rdjavi_idioma_espanol.mp4",
        "title": "Configurando el idioma espanol en GTA 6 por primera vez #Shorts #GTA6 #Espanol",
        "start": 280.0,
        "duration": 40.0,
    },
]

qa = MediaQA()

for idx, item in enumerate(CLIPS_CONFIG, start=1):
    out_file = dest_dir / item["filename"]
    if out_file.exists():
        print(f"✓ Ya existe: {item['filename']}")
        continue

    print(f"\n[{idx}/5] Procesando {item['filename']} ({item['start']}s - {item['start'] + item['duration']}s)...")

    # Extraer subtítulos relativos (recordemos que section_8m_14m empieza en 480s global)
    global_start = 480.0 + item["start"]
    global_end = global_start + item["duration"]

    clip_cues = []
    cue_idx = 1
    for cue in all_cues:
        if cue["end"] > global_start and cue["start"] < global_end:
            rel_cue_start = max(0.0, cue["start"] - global_start)
            rel_cue_end = min(item["duration"], cue["end"] - global_start)
            if rel_cue_end > rel_cue_start + 0.2:
                clip_cues.append(
                    f"{cue_idx}\n{sec_to_tc(rel_cue_start)} --> {sec_to_tc(rel_cue_end)}\n{cue['text']}\n"
                )
                cue_idx += 1

    srt_clip_file = dest_dir / f"{out_file.stem}.srt"
    srt_clip_file.write_text("\n".join(clip_cues), encoding="utf-8")

    # Escapar ruta para filtro de ffmpeg en Windows (sustituir barras invertidas y dos puntos)
    srt_escaped = str(srt_clip_file.resolve()).replace("\\", "/").replace(":", "\\:")

    # Filtro completo: Split background blur 1080x1920 + foreground + subtítulos nítidos en Safe Zone
    # PrimaryColour=&H0000FFFF (Amarillo brillante estilo Shorts), OutlineColour=&H00000000 (Borde negro), MarginV=340
    style_str = (
        "FontName=Arial,FontSize=20,Bold=1,PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,"
        "BorderStyle=1,Outline=2.5,Shadow=0,Alignment=2,MarginV=340"
    )

    if clip_cues:
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_blur];"
            f"[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2,"
            f"subtitles='{srt_escaped}':force_style='{style_str}'[outv]"
        )
    else:
        vf = (
            f"[0:v]split=2[bg][fg];"
            f"[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_blur];"
            f"[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled];"
            f"[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[outv]"
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
        "-crf", "21",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        str(out_file),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0 and out_file.exists():
        rep = qa.validate_clip(out_file, platform="youtube_shorts", max_duration_sec=60.0)
        print(f"  ✓ Generado con éxito: {item['filename']} ({out_file.stat().st_size / (1024*1024):.2f} MB, {rep.duration_sec:.1f}s, QA: {rep.overall_status})")
    else:
        print(f"  ✗ Error renderizando {item['filename']}:\n{res.stderr[-500:]}")

print("\n=== Todos los clips de Fuente C generados exitosamente ===")

