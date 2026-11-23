"""Alineación temporal entre reloj del stream, captura local y telemetría de chat/API."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from clipflow.live_peak.models import TelemetrySample


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class TimeAligner:
    """
    Reconcilia desfase entre reloj de contenido del directo y reloj de pared/APIs externas.
    Marca muestras tardías y discontinuidades (gaps) sin inventar fotogramas ni duplicar eventos.
    """

    def __init__(self, expected_step_ms: int = 5000, max_late_drift_ms: int = 30000):
        self.expected_step_ms = max(1000, int(expected_step_ms))
        self.max_late_drift_ms = max(self.expected_step_ms, int(max_late_drift_ms))
        self._last_clock_by_source: Dict[str, int] = {}
        self._last_wall_by_source: Dict[str, datetime] = {}

    def align_sample(
        self,
        sample: TelemetrySample,
        stream_origin_utc: Optional[datetime] = None,
    ) -> TelemetrySample:
        w_start = _ensure_utc(sample.window_start_utc)
        w_end = _ensure_utc(sample.window_end_utc)
        sample.window_start_utc = w_start
        sample.window_end_utc = w_end

        if sample.source_clock_ms <= 0 and stream_origin_utc is not None:
            origin = _ensure_utc(stream_origin_utc)
            sample.source_clock_ms = max(0, int((w_start - origin).total_seconds() * 1000))

        prev_clock = self._last_clock_by_source.get(sample.source_id)
        prev_wall = self._last_wall_by_source.get(sample.source_id)

        if prev_clock is not None:
            if sample.source_clock_ms < prev_clock:
                sample.late_sample = True
            else:
                delta_clock = sample.source_clock_ms - prev_clock
                if delta_clock > self.expected_step_ms * 3:
                    sample.gap_detected = True
                    sample.gap_ms = max(0, delta_clock - self.expected_step_ms)

        if prev_wall is not None and not sample.late_sample:
            wall_diff_ms = int((w_start - prev_wall).total_seconds() * 1000)
            if wall_diff_ms < -self.max_late_drift_ms:
                sample.late_sample = True

        if not sample.late_sample:
            self._last_clock_by_source[sample.source_id] = max(prev_clock or 0, sample.source_clock_ms)
            self._last_wall_by_source[sample.source_id] = w_start

        return sample

    def checkpoint(self, source_id: str) -> Dict[str, Any]:
        last_wall = self._last_wall_by_source.get(source_id)
        return {
            "last_clock_ms": self._last_clock_by_source.get(source_id, 0),
            "last_wall_utc": last_wall.isoformat() if last_wall else None,
        }

    def restore(self, source_id: str, state: Optional[Dict[str, Any]]) -> None:
        if not state:
            return
        if "last_clock_ms" in state and state["last_clock_ms"] is not None:
            self._last_clock_by_source[source_id] = int(state["last_clock_ms"])
        if state.get("last_wall_utc"):
            try:
                self._last_wall_by_source[source_id] = _ensure_utc(datetime.fromisoformat(state["last_wall_utc"]))
            except ValueError:
                pass
