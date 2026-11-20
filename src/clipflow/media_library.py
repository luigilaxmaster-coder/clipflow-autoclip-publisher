"""Gestión de biblioteca multimedia (Media Library) e integración con el pipeline de AutoClip."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.media_qa import MediaQA
from clipflow.models import MediaAsset, Render, utc_now

logger = logging.getLogger(__name__)

THUMBNAILS_DIR = Path("data") / "thumbnails"


class MediaLibraryService:
    def __init__(self, db: Session):
        self.db = db
        THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def generate_thumbnail_ffmpeg(video_path: str, thumb_path: Optional[str] = None, timestamp_sec: float = 1.0) -> Optional[str]:
        """Extrae un fotograma clave usando FFmpeg local como miniatura."""
        src = Path(video_path)
        if not src.is_file():
            return None

        if not thumb_path:
            THUMBNAILS_DIR.mkdir(parents=True, exist_ok=True)
            stem = src.stem
            thumb_path = str(THUMBNAILS_DIR / f"{stem}_thumb.jpg")

        dest = Path(thumb_path)
        if dest.is_file() and dest.stat().st_size > 0:
            return str(dest)

        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(timestamp_sec),
            "-i", str(src),
            "-vframes", "1",
            "-q:v", "2",
            str(dest),
        ]
        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=15)
            if res.returncode == 0 and dest.is_file():
                return str(dest)
            logger.warning("FFmpeg no pudo generar miniatura para %s: %s", video_path, res.stderr[:150])
        except Exception as e:
            logger.warning("Excepción al ejecutar FFmpeg para miniatura: %s", e)
        return None

    def sync_renders_from_autoclip(self) -> int:
        """
        Sincroniza todos los renders terminados de AutoClip a la Media Library
        sin duplicar los archivos de video en disco.
        """
        renders = self.db.query(Render).filter(Render.video_path != None).all()
        synced_count = 0

        for r in renders:
            if not r.video_path or not Path(r.video_path).is_file():
                continue

            existing = self.db.query(MediaAsset).filter(MediaAsset.render_id == r.id).first()
            if existing:
                continue

            # Generar miniatura
            thumb = r.cover_path
            if not thumb or not Path(thumb).is_file():
                thumb = self.generate_thumbnail_ffmpeg(r.video_path)

            asset = MediaAsset(
                title=f"Clip {r.target_platform.upper()} - {Path(r.video_path).stem}",
                file_path=r.video_path,
                file_type="video",
                thumbnail_path=thumb,
                render_id=r.id,
                width=r.width or 1080,
                height=r.height or 1920,
                duration_sec=r.duration_sec,
                tags_json=json.dumps([r.target_platform, "autoclip"]),
                sha256=r.video_sha256,
                created_at=r.created_at or utc_now(),
            )
            self.db.add(asset)
            synced_count += 1

        if synced_count > 0:
            self.db.commit()
            logger.info("Sincronizados %d renders de AutoClip a Media Library.", synced_count)

        return synced_count

    def scan_local_directory(self, folder_path: str) -> int:
        """Importa videos desde un directorio local o sincronizado en la nube."""
        p = Path(folder_path)
        if not p.is_dir():
            raise FileNotFoundError(f"El directorio no existe: {folder_path}")

        imported = 0
        validator = MediaQA()

        for ext in ("*.mp4", "*.mov", "*.mkv"):
            for video_file in p.glob(ext):
                path_str = str(video_file.resolve())
                existing = self.db.query(MediaAsset).filter(MediaAsset.file_path == path_str).first()
                if existing:
                    continue

                # Extraer metadatos con MediaQA
                report = validator.validate_clip(path_str)
                thumb = self.generate_thumbnail_ffmpeg(path_str)

                # Calcular hash SHA-256
                hasher = hashlib.sha256()
                with open(video_file, "rb") as f:
                    for chunk in iter(lambda: f.read(65536), b""):
                        hasher.update(chunk)

                asset = MediaAsset(
                    title=video_file.stem.replace("_", " ").title(),
                    file_path=path_str,
                    file_type="video",
                    thumbnail_path=thumb,
                    width=report.width or 1080,
                    height=report.height or 1920,
                    duration_sec=report.duration_sec,
                    tags_json=json.dumps(["imported", video_file.suffix.lower().strip(".")]),
                    sha256=hasher.hexdigest(),
                    created_at=utc_now(),
                )
                self.db.add(asset)
                imported += 1

        if imported > 0:
            self.db.commit()
        return imported

    def list_assets(
        self,
        search: Optional[str] = None,
        tag: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[MediaAsset]:
        """Consulta los recursos multimedia de la biblioteca."""
        q = self.db.query(MediaAsset).order_by(MediaAsset.created_at.desc())
        if search:
            q = q.filter(MediaAsset.title.ilike(f"%{search}%"))
        if tag:
            q = q.filter(MediaAsset.tags_json.ilike(f"%{tag}%"))
        return q.offset(offset).limit(limit).all()

    def get_asset(self, asset_id: str) -> Optional[MediaAsset]:
        return self.db.query(MediaAsset).filter(MediaAsset.id == asset_id).first()
