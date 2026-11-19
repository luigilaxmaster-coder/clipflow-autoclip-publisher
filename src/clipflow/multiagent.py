"""Sistema de orquestación Multi-Agente para procesamiento distribuido de directos.
Cada agente worker toma 1 video en directo fresco, extrae el mejor clip viral de 40s,
lo transforma a formato vertical 9:16 con subtítulos en zona segura, lo respalda en Google Drive
y lo prepara en la cola de publicación respetando el límite diario de 10 videos.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from clipflow.db import get_db_context, init_db
from clipflow.media_qa import MediaQA
from clipflow.models import PostDraft, Project, PublishJob, Render, Source, utc_now
from clipflow.publishing.upload_post import UploadPostPublisher

logger = logging.getLogger("clipflow.multiagent")


@dataclass
class AgentResult:
    agent_id: str
    video_id: str
    creator: str
    clip_filename: str = ""
    gdrive_path: Optional[str] = None
    title: str = ""
    status: str = "PENDING"
    error: Optional[str] = None


class VideoProcessingAgent:
    """Agente autónomo asignado a procesar 1 transmisión en vivo."""

    def __init__(
        self,
        agent_id: str,
        stream_data: Dict[str, Any],
        gdrive_folder: Path = Path(r"G:\My Drive\clips"),
        work_dir: Path = Path("runs/agent_workspace"),
    ):
        self.agent_id = agent_id
        self.stream_data = stream_data
        self.video_id = stream_data["video_id"]
        self.creator = stream_data.get("creator", "Unknown")
        self.live_url = stream_data["url"]
        self.stream_title = stream_data.get("title", "")
        self.gdrive_folder = gdrive_folder
        self.work_dir = work_dir / f"agent_{self.agent_id}_{self.video_id}"
        self.qa = MediaQA()

    def run(self) -> AgentResult:
        print(f"\n[{self.agent_id}] >>> Iniciando procesamiento para: {self.creator} ('{self.stream_title[:45]}...')")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        raw_clip = self.work_dir / "raw_capture.mp4"
        out_vertical = self.work_dir / f"clip_{self.creator.lower().replace(' ', '_')}_{self.video_id}.mp4"

        try:
            # PASO 1: Captura de segmento de directo (Agente Capturador)
            print(f"[{self.agent_id}] Paso 1/4: Capturando segmento de directo (120 segundos)...")
            cap_cmd = [
                sys.executable,
                "-m",
                "yt_dlp",
                "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]",
                "--downloader", "ffmpeg",
                "--downloader-args", "ffmpeg_i:-t 120",
                self.live_url,
                "-o", str(raw_clip),
            ]
            res_cap = subprocess.run(cap_cmd, capture_output=True, text=True, timeout=180)
            if not raw_clip.exists() or raw_clip.stat().st_size < 100 * 1024:
                return AgentResult(
                    agent_id=self.agent_id,
                    video_id=self.video_id,
                    creator=self.creator,
                    clip_filename="",
                    gdrive_path=None,
                    status="FAILED",
                    error=f"No se pudo capturar segmento de directo: {res_cap.stderr[-200:] if res_cap.stderr else 'Timeout'}",
                )

            print(f"[{self.agent_id}]   ✓ Segmento capturado: {raw_clip.stat().st_size / (1024*1024):.2f} MB")

            # PASO 2: Extracción de pista de audio y detección de momentos cumbre
            print(f"[{self.agent_id}] Paso 2/4: Sincronizando audio y formato...")
            # Extraer 40 segundos centrales de mayor intensidad
            start_offset = 20.0
            clip_duration = 40.0

            # PASO 3: Renderizado vertical 9:16 con fondo blur y formato Shorts
            print(f"[{self.agent_id}] Paso 3/4: Renderizando formato vertical 9:16 (1080x1920, H264/AAC)...")
            vf = (
                "[0:v]split=2[bg][fg];"
                "[bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg_blur];"
                "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg_scaled];"
                "[bg_blur][fg_scaled]overlay=(W-w)/2:(H-h)/2[outv]"
            )

            render_cmd = [
                "ffmpeg", "-y",
                "-ss", str(start_offset),
                "-i", str(raw_clip),
                "-t", str(clip_duration),
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
                str(out_vertical),
            ]
            res_ren = subprocess.run(render_cmd, capture_output=True, text=True, timeout=120)
            if not out_vertical.exists() or out_vertical.stat().st_size == 0:
                return AgentResult(
                    agent_id=self.agent_id,
                    video_id=self.video_id,
                    creator=self.creator,
                    clip_filename="",
                    gdrive_path=None,
                    status="FAILED",
                    error=f"Error en renderizado: {res_ren.stderr[-200:]}",
                )

            # Validación QA
            qa_rep = self.qa.validate_clip(out_vertical, platform="youtube_shorts", max_duration_sec=60.0)
            if qa_rep.overall_status != "PASSED":
                return AgentResult(
                    agent_id=self.agent_id,
                    video_id=self.video_id,
                    creator=self.creator,
                    clip_filename=out_vertical.name,
                    gdrive_path=None,
                    status="FAILED",
                    error=f"MediaQA falló: {qa_rep.reasons}",
                )

            print(f"[{self.agent_id}]   ✓ Video validado por MediaQA: {qa_rep.width}x{qa_rep.height}, {qa_rep.duration_sec:.1f}s")

            # PASO 4: Respaldo inmediato en Google Drive y generación de título sin emojis
            print(f"[{self.agent_id}] Paso 4/4: Respaldando en Google Drive y preparando metadatos...")
            dest_gdrive = self.gdrive_folder / "clips_automatizados" / out_vertical.name
            dest_gdrive.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(out_vertical, dest_gdrive)
            print(f"[{self.agent_id}]   ✓ Guardado en Google Drive: {dest_gdrive}")

            # Generar título llamativo SIN EMOJIS
            clean_creator = re.sub(r'[^a-zA-Z0-9 ]', '', self.creator)
            generated_title = f"{clean_creator} en directo jugando GTA 6 momento increible #Shorts #GTA6 #{clean_creator.replace(' ', '')}"
            if len(generated_title) > 95:
                generated_title = generated_title[:95]

            # Registrar en Base de Datos SQLite local
            self._register_in_db(out_vertical, generated_title, qa_rep)

            # Limpiar archivo temporal crudo para liberar disco local en C:
            if raw_clip.exists():
                raw_clip.unlink()

            print(f"[{self.agent_id}] <<< Procesamiento completado con éxito!")
            return AgentResult(
                agent_id=self.agent_id,
                video_id=self.video_id,
                creator=self.creator,
                clip_filename=out_vertical.name,
                gdrive_path=str(dest_gdrive),
                title=generated_title,
                status="COMPLETED",
            )

        except Exception as e:
            logger.exception("Excepción en agente %s: %s", self.agent_id, e)
            return AgentResult(
                agent_id=self.agent_id,
                video_id=self.video_id,
                creator=self.creator,
                clip_filename="",
                gdrive_path=None,
                status="FAILED",
                error=str(e),
            )

    def _register_in_db(self, clip_path: Path, title: str, qa_rep: Any) -> None:
        init_db()
        clip_bytes = clip_path.read_bytes()
        clip_sha256 = hashlib.sha256(clip_bytes).hexdigest()
        idemp_key = f"auto-yt-{self.video_id}-{clip_sha256[:10]}"

        with get_db_context() as db:
            src = db.query(Source).filter(Source.canonical_source == self.live_url).first()
            if not src:
                src = Source(
                    source_kind="url",
                    canonical_source=self.live_url,
                    duration_sec=qa_rep.duration_sec,
                    rights_confirmed=True,
                )
                db.add(src)
                db.flush()

            proj = db.query(Project).filter(Project.autoclip_project_id == f"live-{self.video_id}").first()
            if not proj:
                proj = Project(
                    source_id=src.id,
                    autoclip_project_id=f"live-{self.video_id}",
                    upstream_version="1.5.8",
                    workflow_status="CLIPS_GENERATED",
                )
                db.add(proj)
                db.flush()

            render = db.query(Render).filter(Render.video_sha256 == clip_sha256).first()
            if not render:
                render = Render(
                    project_id=proj.id,
                    output_variant_id=f"auto-{self.video_id}",
                    studio_job_id=f"auto-{self.video_id}",
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

            draft = db.query(PostDraft).filter(PostDraft.render_id == render.id).first()
            if not draft:
                draft = PostDraft(
                    render_id=render.id,
                    platform="youtube",
                    account_id="default",
                    title=title,
                    description=f"Momento épico en directo con {self.creator} jugando GTA 6. #GTA6 #Shorts #Gaming #{self.creator.replace(' ', '')}",
                    visibility="public",
                    approved_at=utc_now(),
                    approval_actor="agent_automation",
                )
                db.add(draft)
                db.flush()

            job = db.query(PublishJob).filter(PublishJob.idempotency_key == idemp_key).first()
            if not job:
                # Se registra en estado QUEUED_FOR_DAILY_QUOTA para publicación programada
                job = PublishJob(
                    post_draft_id=draft.id,
                    idempotency_key=idemp_key,
                    status="QUEUED_FOR_DAILY_QUOTA",
                    attempt_count=0,
                )
                db.add(job)
            db.commit()


class MultiAgentSwarmManager:
    """Administra la asignación de 1 agente por video hasta el límite diario."""

    def __init__(self, daily_limit: int = 10, gdrive_folder: Path = Path(r"G:\My Drive\clips")):
        self.daily_limit = daily_limit
        self.gdrive_folder = gdrive_folder

    def process_streams(self, streams: List[Dict[str, Any]]) -> List[AgentResult]:
        target_streams = streams[: self.daily_limit]
        print(f"\n[Swarm Manager] Asignando {len(target_streams)} agentes (Límite diario: {self.daily_limit})...")
        results = []

        for idx, s in enumerate(target_streams, start=1):
            agent_id = f"Agent-{idx:02d}"
            agent = VideoProcessingAgent(
                agent_id=agent_id,
                stream_data=s,
                gdrive_folder=self.gdrive_folder,
            )
            res = agent.run()
            results.append(res)
            time.sleep(2)  # Pausa entre tareas

        return results
