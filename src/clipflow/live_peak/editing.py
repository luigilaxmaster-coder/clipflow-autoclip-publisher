"""Edición y validación técnica reutilizando AutoClipAdapter y MediaQA (sin reimplementar FFmpeg)."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from clipflow.autoclip_adapter import AutoClipAdapter
from clipflow.media_qa import MediaQA

logger = logging.getLogger(__name__)


def compute_content_hash(
    video_path: Optional[str],
    title: str = "",
    onset_ms: int = 0,
    end_ms: int = 0,
) -> str:
    h = hashlib.sha256()
    if video_path and Path(video_path).is_file():
        p = Path(video_path)
        h.update(f"{p.name}:{p.stat().st_size}".encode("utf-8"))
        with p.open("rb") as f:
            h.update(f.read(65536))
    else:
        h.update(f"{title}|{onset_ms}|{end_ms}".encode("utf-8"))
    return h.hexdigest()


class LivePeakEditor:
    """
    Prepara candidatos verticales 9:16 desde el episodio capturado delegando el renderizado
    a `AutoClipAdapter` y ejecutando inspección estricta `MediaQA`.
    """

    def __init__(
        self,
        adapter: Optional[AutoClipAdapter] = None,
        qa: Optional[MediaQA] = None,
    ):
        self.adapter = adapter or AutoClipAdapter()
        self.qa = qa or MediaQA()

    def build_episode_span(
        self,
        onset_ms: int,
        end_ms: int,
        prerecord_ms: int = 45000,
        postrecord_ms: int = 30000,
        target_min_sec: int = 20,
        target_max_sec: int = 60,
    ) -> Dict[str, Any]:
        """Conserva setup + sorpresa + payoff + reacción dentro de duración recomendada."""
        raw_start = max(0, int(onset_ms) - int(prerecord_ms))
        raw_end = max(raw_start + target_min_sec * 1000, int(end_ms) + int(postrecord_ms))
        duration_ms = raw_end - raw_start
        max_ms = target_max_sec * 1000
        if duration_ms > max_ms:
            # Centrar alrededor del pico conservando contexto previo y reacción final
            peak_center = (int(onset_ms) + int(end_ms)) // 2
            clip_start = max(0, peak_center - int(max_ms * 0.45))
            clip_end = clip_start + max_ms
        else:
            clip_start, clip_end = raw_start, raw_end

        return {
            "start_ms": clip_start,
            "end_ms": clip_end,
            "duration_sec": round((clip_end - clip_start) / 1000.0, 2),
        }

    def produce_variants_from_source(
        self,
        source_video_path: str,
        *,
        platforms: Optional[List[str]] = None,
        srt_path: Optional[str] = None,
        run_autoclip: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Cuando `run_autoclip=True`, delega en `AutoClipAdapter.run_produce_python`.
        Si ya existe un archivo renderizado vertical válido, ejecuta `MediaQA.check()`.
        """
        variants: List[Dict[str, Any]] = []
        if run_autoclip:
            res = self.adapter.run_produce_python(
                source=source_video_path,
                platforms=platforms or ["youtube_shorts", "tiktok"],
                srt_path=srt_path,
            )
            extracted = self.adapter.extract_output_variants(res)
            for item in extracted:
                v_path = item.get("video_path")
                qa_ok = bool(v_path and Path(v_path).is_file() and self.qa.validate_clip(v_path).passed)
                variants.append({
                    **item,
                    "media_qa_passed": qa_ok,
                    "content_hash": compute_content_hash(v_path, item.get("title") or ""),
                })
            return variants

        if source_video_path and Path(source_video_path).is_file():
            qa_res = self.qa.validate_clip(source_video_path)
            variants.append({
                "output_variant_id": "direct_source_variant",
                "video_path": source_video_path,
                "duration_sec": qa_res.duration_sec,
                "media_qa_passed": qa_res.passed,
                "qa_reason": qa_res.overall_status,
                "content_hash": compute_content_hash(source_video_path),
            })
        return variants
