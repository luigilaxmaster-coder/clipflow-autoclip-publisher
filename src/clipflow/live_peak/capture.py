"""Buffer circular en disco (ring-buffer) de segmentos de directo 4-6s y preservación de episodios."""
from __future__ import annotations

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class RingSegment:
    index: int
    file_path: str
    start_ms: int
    end_ms: int
    duration_ms: int
    has_gap: bool = False


class SegmentRingBuffer:
    """
    Administra segmentos cortos (4-6 s) generados por FFmpeg en un anillo reteniendo
    `ring_buffer_minutes`. Cuando un evento pasa a captura, congela el manifiesto
    incluyendo pre-roll y post-roll sin copiar ni perder datos previos.
    """

    def __init__(
        self,
        source_id: str,
        base_dir: Optional[Path] = None,
        segment_seconds: int = 5,
        retention_minutes: int = 30,
    ):
        self.source_id = source_id
        self.base_dir = Path(base_dir or Path("data") / "live_ring" / source_id)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.segment_seconds = max(2, min(10, int(segment_seconds)))
        self.retention_ms = max(60000, int(retention_minutes) * 60 * 1000)
        self.index_path = self.base_dir / "ring_index.json"
        self.segments: List[RingSegment] = self._load_index()

    def _load_index(self) -> List[RingSegment]:
        if not self.index_path.exists():
            return []
        try:
            raw = json.loads(self.index_path.read_text(encoding="utf-8"))
            return [RingSegment(**item) for item in raw if isinstance(item, dict)]
        except Exception:
            return []

    def _save_index(self) -> None:
        self.index_path.write_text(
            json.dumps([s.__dict__ for s in self.segments], indent=2),
            encoding="utf-8",
        )

    def register_segment(
        self,
        file_path: str,
        start_ms: int,
        end_ms: int,
        has_gap: bool = False,
    ) -> RingSegment:
        idx = (self.segments[-1].index + 1) if self.segments else 0
        seg = RingSegment(
            index=idx,
            file_path=str(file_path),
            start_ms=max(0, int(start_ms)),
            end_ms=max(int(start_ms) + 1000, int(end_ms)),
            duration_ms=max(1000, int(end_ms) - int(start_ms)),
            has_gap=bool(has_gap),
        )
        self.segments.append(seg)
        self.prune_expired()
        self._save_index()
        return seg

    def prune_expired(self, pinned_ranges_ms: Optional[List[tuple[int, int]]] = None) -> int:
        """Elimina segmentos antiguos fuera de ventana de retención que no pertenezcan a episodios protegidos."""
        if not self.segments:
            return 0
        latest_end = self.segments[-1].end_ms
        cutoff = latest_end - self.retention_ms
        pinned = list(pinned_ranges_ms or [])

        kept: List[RingSegment] = []
        removed = 0
        for seg in self.segments:
            is_pinned = any(not (seg.end_ms < p_start or seg.start_ms > p_end) for p_start, p_end in pinned)
            if seg.end_ms >= cutoff or is_pinned:
                kept.append(seg)
            else:
                removed += 1
                p = Path(seg.file_path)
                if p.exists() and p.parent == self.base_dir:
                    try:
                        p.unlink()
                    except OSError:
                        pass
        self.segments = kept
        return removed

    def freeze_episode_manifest(
        self,
        event_id: str,
        onset_ms: int,
        end_ms: int,
        prerecord_ms: int = 45000,
        postrecord_ms: int = 30000,
    ) -> str:
        """Guarda un manifiesto auditable de segmentos que cubren `[onset - pre_roll, end + post_roll]`."""
        span_start = max(0, onset_ms - prerecord_ms)
        span_end = max(span_start + 1000, end_ms + postrecord_ms)
        covered = [s for s in self.segments if s.end_ms >= span_start and s.start_ms <= span_end]

        manifest_dir = self.base_dir / "manifests"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"episode_{event_id}.json"
        payload: Dict[str, Any] = {
            "event_id": event_id,
            "source_id": self.source_id,
            "span_start_ms": span_start,
            "span_end_ms": span_end,
            "segment_count": len(covered),
            "has_gap": any(s.has_gap for s in covered),
            "segments": [s.__dict__ for s in covered],
        }
        manifest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(manifest_path)

    def build_ffmpeg_ring_cmd(self, stream_input_url: str) -> List[str]:
        """Construye comando FFmpeg oficial para capturar segmentos rotativos sin recodificar en exceso."""
        out_pattern = str(self.base_dir / "seg_%06d.ts")
        return [
            "ffmpeg",
            "-y",
            "-i",
            stream_input_url,
            "-c",
            "copy",
            "-f",
            "segment",
            "-segment_time",
            str(self.segment_seconds),
            "-reset_timestamps",
            "1",
            out_pattern,
        ]
