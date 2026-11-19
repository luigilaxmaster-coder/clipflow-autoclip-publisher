"""Selector de clips: filtrado, ranking, descarte de solapamientos y deduplicación."""
from __future__ import annotations

import logging
from typing import Any, Dict, List
from clipflow.models import ClipCandidate

logger = logging.getLogger(__name__)


class ClipSelector:
    def __init__(
        self,
        max_clips: int = 10,
        min_score: float = 0.6,
        min_spacing_sec: float = 8.0,
        min_duration_sec: float = 15.0,
        max_duration_sec: float = 180.0,
    ):
        self.max_clips = max_clips
        self.min_score = min_score
        self.min_spacing_sec = min_spacing_sec
        self.min_duration_sec = min_duration_sec
        self.max_duration_sec = max_duration_sec

    def select_candidates(
        self,
        raw_candidates: List[Dict[str, Any]],
        project_id: str,
    ) -> List[ClipCandidate]:
        """
        Filtra y clasifica candidatos según calidad, relevancia y restricciones temporales.
        No rellena artificialmente hasta 10 si solo hay menos segmentos con alta puntuación.
        """
        parsed: List[Dict[str, Any]] = []

        for item in raw_candidates:
            start_ms = int(item.get("start_ms", item.get("start_time_ms", 0)))
            end_ms = int(item.get("end_ms", item.get("end_time_ms", 0)))
            duration_sec = (end_ms - start_ms) / 1000.0
            score = float(item.get("score", item.get("quality_score", 0.0)))
            reason = str(item.get("reason", item.get("outline", item.get("title", ""))))
            group = str(item.get("semantic_group", item.get("category", "default")))

            # Descarte si no cumple duración básica o score mínimo
            if duration_sec < self.min_duration_sec or duration_sec > self.max_duration_sec:
                continue

            parsed.append({
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_sec": duration_sec,
                "score": score,
                "reason": reason,
                "group": group,
                "raw": item,
            })

        # Ordenar por puntuación descendente
        parsed.sort(key=lambda x: x["score"], reverse=True)

        selected_intervals: List[tuple[int, int]] = []
        out_candidates: List[ClipCandidate] = []
        spacing_ms = int(self.min_spacing_sec * 1000)

        for c in parsed:
            start = c["start_ms"]
            end = c["end_ms"]

            # Comprobar si solapa o está demasiado cerca de un clip ya elegido
            is_overlap = False
            for s_start, s_end in selected_intervals:
                # Comprobar si hay intersección o distancia menor al espaciado mínimo
                if not (end + spacing_ms <= s_start or start >= s_end + spacing_ms):
                    is_overlap = True
                    break

            if is_overlap:
                candidate = ClipCandidate(
                    project_id=project_id,
                    source_start_ms=start,
                    source_end_ms=end,
                    score=c["score"],
                    reason=f"Descartado por solapamiento con otro segmento de mayor puntuación: {c['reason']}",
                    semantic_group=c["group"],
                    selection_status="duplicate",
                )
                out_candidates.append(candidate)
                continue

            if c["score"] < self.min_score:
                candidate = ClipCandidate(
                    project_id=project_id,
                    source_start_ms=start,
                    source_end_ms=end,
                    score=c["score"],
                    reason=f"Descartado por puntuación insuficiente ({c['score']:.2f} < {self.min_score}): {c['reason']}",
                    semantic_group=c["group"],
                    selection_status="dropped",
                )
                out_candidates.append(candidate)
                continue

            if len(selected_intervals) < self.max_clips:
                selected_intervals.append((start, end))
                candidate = ClipCandidate(
                    project_id=project_id,
                    source_start_ms=start,
                    source_end_ms=end,
                    score=c["score"],
                    reason=c["reason"] or f"Clip de alto impacto (Score {c['score']:.2f})",
                    semantic_group=c["group"],
                    selection_status="selected",
                )
                out_candidates.append(candidate)
            else:
                candidate = ClipCandidate(
                    project_id=project_id,
                    source_start_ms=start,
                    source_end_ms=end,
                    score=c["score"],
                    reason=f"Excede cuota máxima de {self.max_clips} clips: {c['reason']}",
                    semantic_group=c["group"],
                    selection_status="dropped",
                )
                out_candidates.append(candidate)

        return out_candidates
