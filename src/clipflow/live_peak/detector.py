"""Detector estadístico de picos multi-señal con histéresis, cooldown y fusión de episodios."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from clipflow.live_peak.baseline import RobustBaseline
from clipflow.live_peak.models import (
    DetectorSignalResult,
    PeakCandidateEvent,
    TelemetrySample,
)

DEFAULT_SIGNAL_WEIGHTS: Dict[str, float] = {
    "chat": 0.25,
    "audience": 0.10,
    "audio": 0.15,
    "video": 0.15,
    "semantic": 0.20,
    "novelty": 0.10,
    "topic": 0.05,
}


@dataclass
class _SourceSessionState:
    state: str = "WATCHING"  # WATCHING | RISING | PEAK_ACTIVE | FALLING
    consecutive_enter_count: int = 0
    onset_ms: int = 0
    peak_ms: int = 0
    max_hype: float = 0.0
    max_peak_score: float = 0.0
    fall_start_ms: Optional[int] = None
    cooldown_until_ms: int = 0
    has_gap: bool = False
    gap_ms: int = 0
    evidence_families: set[str] = field(default_factory=set)
    reason_codes: set[str] = field(default_factory=set)
    samples_in_event: int = 0
    missing_signals_union: set[str] = field(default_factory=set)


class PeakDetector:
    """
    Combina chat, audiencia, audio, video, semántica, novedad y relevancia con
    renormalización sobre señales presentes (`None != 0`), doble corroboración,
    protección contra spam/bots/muestras pequeñas, histéresis y cooldown.
    """

    def __init__(
        self,
        peak_enter: float = 0.72,
        peak_exit: float = 0.45,
        peak_enter_consecutive_windows: int = 2,
        peak_exit_seconds: int = 20,
        cooldown_seconds: int = 90,
        max_event_seconds: int = 240,
        prerecord_seconds: int = 45,
        postrecord_seconds: int = 30,
        require_two_signal_families: bool = True,
        min_distinct_chatters_for_chat_only_trigger: int = 10,
        detector_version: str = "v1.0",
    ):
        self.peak_enter = peak_enter
        self.peak_exit = peak_exit
        self.peak_enter_consecutive_windows = max(1, int(peak_enter_consecutive_windows))
        self.peak_exit_ms = max(5000, int(peak_exit_seconds * 1000))
        self.cooldown_ms = max(0, int(cooldown_seconds * 1000))
        self.max_event_ms = max(30000, int(max_event_seconds * 1000))
        self.prerecord_ms = max(0, int(prerecord_seconds * 1000))
        self.postrecord_ms = max(0, int(postrecord_seconds * 1000))
        self.require_two_signal_families = require_two_signal_families
        self.min_distinct_chatters = max(2, int(min_distinct_chatters_for_chat_only_trigger))
        self.detector_version = detector_version

        self._baselines: Dict[str, RobustBaseline] = {}
        self._sessions: Dict[str, _SourceSessionState] = {}

    def _baseline(self, source_id: str) -> RobustBaseline:
        if source_id not in self._baselines:
            self._baselines[source_id] = RobustBaseline()
        return self._baselines[source_id]

    def _session(self, source_id: str) -> _SourceSessionState:
        if source_id not in self._sessions:
            self._sessions[source_id] = _SourceSessionState()
        return self._sessions[source_id]

    def _compute_family_scores(
        self, sample: TelemetrySample
    ) -> Tuple[Dict[str, Optional[float]], List[str], bool, List[str]]:
        bl = self._baseline(sample.source_id)
        reason_codes: List[str] = []
        missing_signals: List[str] = []

        # 1. Chat (considerando bots/spam y mensajes por espectador si existe)
        spam_filtered = RobustBaseline.is_spam_burst(
            bot_ratio=sample.bot_ratio,
            duplicate_ratio=sample.duplicate_ratio,
            unique_chatters=sample.unique_chatters,
            total_messages=sample.total_messages,
        )
        chat_score: Optional[float] = None
        if sample.chat_rate is None:
            missing_signals.append("chat")
        elif spam_filtered:
            chat_score = 0.0
            reason_codes.append("SPAM_FILTERED")
        else:
            effective_rate = sample.chat_rate
            if sample.concurrent_viewers and sample.concurrent_viewers > 0:
                effective_rate = (sample.chat_rate / max(10, sample.concurrent_viewers)) * 100.0
            c_rate_score, z_chat, _ = bl.score_and_update("chat_rate", effective_rate, use_log1p=True)
            c_uniq_score: Optional[float] = None
            if sample.unique_chatters is not None:
                c_uniq_score, _, _ = bl.score_and_update("unique_chatters", float(sample.unique_chatters), use_log1p=True)
            if sample.unique_chatters is not None and sample.unique_chatters < self.min_distinct_chatters:
                # Evita que 2->8 mensajes en un canal muy pequeño disparen falso pico por sí solos
                chat_score = min(0.45, c_rate_score or 0.0)
                reason_codes.append("SMALL_SAMPLE_CHAT_GUARD")
            else:
                parts = [p for p in (c_rate_score, c_uniq_score) if p is not None]
                chat_score = sum(parts) / len(parts) if parts else None
                if z_chat >= 2.5:
                    reason_codes.append("CHAT_SPIKE")
                if c_uniq_score is not None and c_uniq_score >= 0.65:
                    reason_codes.append("UNIQUE_CHATTERS_UP")

        # 2. Audiencia (concurrentes)
        audience_score: Optional[float] = None
        if sample.audience_delta is None and sample.concurrent_viewers is None:
            missing_signals.append("audience")
        else:
            aud_val = sample.audience_delta
            if aud_val is None and sample.concurrent_viewers is not None:
                aud_val = float(sample.concurrent_viewers)
            audience_score, z_aud, _ = bl.score_and_update("audience", aud_val, use_log1p=False)
            if audience_score is not None and z_aud >= 2.0:
                reason_codes.append("AUDIENCE_SURGE")

        # 3. Audio
        audio_score: Optional[float] = None
        if sample.audio_energy is None and sample.audio_laughter_or_reaction is None:
            missing_signals.append("audio")
        else:
            a1, _, _ = bl.score_and_update("audio_energy", sample.audio_energy, epsilon=0.15)
            a2 = sample.audio_laughter_or_reaction
            parts = [p for p in (a1, a2) if p is not None]
            audio_score = max(parts) if parts else None
            if audio_score is not None and audio_score >= 0.65:
                reason_codes.append("REACTION_AUDIO_UP")

        # 4. Video
        video_score: Optional[float] = None
        if sample.visual_motion is None and sample.scene_change is None:
            missing_signals.append("video")
        else:
            v1, _, _ = bl.score_and_update("visual_motion", sample.visual_motion, epsilon=0.15)
            v2 = sample.scene_change
            parts = [p for p in (v1, v2) if p is not None]
            video_score = max(parts) if parts else None
            if video_score is not None and video_score >= 0.65:
                reason_codes.append("VISUAL_ACTION_SPIKE")

        # 5. Semántica
        semantic_parts = [
            p
            for p in (
                sample.semantic_surprise,
                sample.semantic_payoff_clue,
                sample.semantic_emotion,
            )
            if p is not None
        ]
        semantic_score: Optional[float] = None
        if not semantic_parts:
            missing_signals.append("semantic")
        else:
            semantic_score = round(sum(semantic_parts) / len(semantic_parts), 4)
            if semantic_score >= 0.68:
                reason_codes.append("SEMANTIC_PAYOFF_CLUE")

        # 6. Novedad & Tema
        novelty_score = sample.novelty_score
        if novelty_score is None:
            missing_signals.append("novelty")
        topic_score = sample.topic_relevance
        if topic_score is None:
            missing_signals.append("topic")

        scores = {
            "chat": chat_score,
            "audience": audience_score,
            "audio": audio_score,
            "video": video_score,
            "semantic": semantic_score,
            "novelty": novelty_score,
            "topic": topic_score,
        }
        return scores, missing_signals, spam_filtered, reason_codes

    def process_sample(
        self, sample: TelemetrySample
    ) -> Tuple[DetectorSignalResult, Optional[PeakCandidateEvent]]:
        """
        Procesa una ventana de 5s para un directo continuo (incluso 24+ horas)
        y retorna el estado instantáneo + un `PeakCandidateEvent` cuando un episodio cierra.
        """
        sess = self._session(sample.source_id)
        family_scores, missing_signals, spam_filtered, reason_codes = self._compute_family_scores(sample)

        # Renormalizar pesos estrictamente sobre familias presentes (None != 0)
        present_weight = sum(DEFAULT_SIGNAL_WEIGHTS[k] for k, v in family_scores.items() if v is not None)
        if present_weight <= 0.0:
            hype_score = 0.0
        else:
            weighted_sum = sum(
                float(v) * DEFAULT_SIGNAL_WEIGHTS[k]
                for k, v in family_scores.items()
                if v is not None
            )
            hype_score = round(max(0.0, min(1.0, weighted_sum / present_weight)), 4)

        # Confianza de detección por cobertura de señales disponibles
        signal_coverage = round(present_weight, 4)
        confidence = round(max(0.15, min(1.0, signal_coverage * (0.85 if sample.late_sample else 1.0))), 3)

        active_families = [
            k
            for k, v in family_scores.items()
            if k in ("chat", "audience", "audio", "video", "semantic") and v is not None and v >= 0.60
        ]

        # Reglas anti-falsos-positivos para iniciar pico:
        # >= 2 familias independientes o H >= 0.90 con evidencia semántica corroborada
        semantic_corroborated = (family_scores.get("semantic") or 0.0) >= 0.72
        distinct_chat_ok = (sample.unique_chatters is None) or (sample.unique_chatters >= self.min_distinct_chatters)
        chat_only_spam_guard = active_families == ["chat"] and not distinct_chat_ok

        meets_enter = False
        if not spam_filtered and not chat_only_spam_guard and hype_score >= self.peak_enter:
            if len(active_families) >= 2:
                meets_enter = True
            elif not self.require_two_signal_families and active_families:
                meets_enter = True
            elif hype_score >= 0.90 and semantic_corroborated:
                meets_enter = True

        emitted_event: Optional[PeakCandidateEvent] = None
        clock = sample.source_clock_ms

        if sample.gap_detected and sess.state in ("RISING", "PEAK_ACTIVE", "FALLING"):
            sess.has_gap = True
            sess.gap_ms += sample.gap_ms

        if sess.state == "WATCHING":
            if clock >= sess.cooldown_until_ms and meets_enter:
                sess.consecutive_enter_count = 1
                if sess.consecutive_enter_count >= self.peak_enter_consecutive_windows:
                    self._enter_peak(sess, sample, hype_score, active_families, reason_codes, missing_signals)
                else:
                    sess.state = "RISING"
                    sess.onset_ms = clock
                    sess.max_hype = hype_score
                    sess.peak_ms = clock
                    sess.evidence_families.update(active_families)
                    sess.reason_codes.update(reason_codes)
            else:
                sess.consecutive_enter_count = 0

        elif sess.state == "RISING":
            if meets_enter:
                sess.consecutive_enter_count += 1
                sess.evidence_families.update(active_families)
                sess.reason_codes.update(reason_codes)
                if hype_score > sess.max_hype:
                    sess.max_hype = hype_score
                    sess.peak_ms = clock
                if sess.consecutive_enter_count >= self.peak_enter_consecutive_windows:
                    self._enter_peak(sess, sample, hype_score, active_families, reason_codes, missing_signals)
            else:
                # Falsa subida momentánea
                sess.state = "WATCHING"
                sess.consecutive_enter_count = 0

        elif sess.state in ("PEAK_ACTIVE", "FALLING"):
            sess.samples_in_event += 1
            sess.evidence_families.update(active_families)
            sess.reason_codes.update(reason_codes)
            sess.missing_signals_union.update(missing_signals)
            if hype_score > sess.max_hype:
                sess.max_hype = hype_score
                sess.peak_ms = clock

            event_duration = max(0, clock - sess.onset_ms)
            if event_duration >= self.max_event_ms:
                emitted_event = self._close_event(sess, sample.source_id, clock, confidence, "MAX_EVENT_DURATION")
            elif hype_score < self.peak_exit:
                if sess.state == "PEAK_ACTIVE":
                    sess.state = "FALLING"
                    sess.fall_start_ms = clock
                elif sess.fall_start_ms is not None and (clock - sess.fall_start_ms) >= self.peak_exit_ms:
                    emitted_event = self._close_event(sess, sample.source_id, clock, confidence, "HYSTERESIS_EXIT")
            else:
                sess.state = "PEAK_ACTIVE"
                sess.fall_start_ms = None

        peak_score = round(max(sess.max_hype if sess.state in ("PEAK_ACTIVE", "FALLING") else hype_score, hype_score) * 100.0, 2)
        if missing_signals:
            reason_codes.extend(f"MISSING_{sig.upper()}" for sig in missing_signals)

        result = DetectorSignalResult(
            source_clock_ms=clock,
            hype_score=hype_score,
            confidence=confidence,
            active_families=active_families,
            missing_signals=missing_signals,
            spam_filtered=spam_filtered,
            reason_codes=sorted(set(reason_codes)),
            state=sess.state,
            peak_score=peak_score,
        )
        return result, emitted_event

    def _enter_peak(
        self,
        sess: _SourceSessionState,
        sample: TelemetrySample,
        hype_score: float,
        active_families: List[str],
        reason_codes: List[str],
        missing_signals: List[str],
    ) -> None:
        sess.state = "PEAK_ACTIVE"
        if sess.onset_ms <= 0:
            sess.onset_ms = sample.source_clock_ms
        sess.peak_ms = sample.source_clock_ms
        sess.max_hype = max(sess.max_hype, hype_score)
        sess.max_peak_score = round(sess.max_hype * 100.0, 2)
        sess.fall_start_ms = None
        sess.samples_in_event = 1
        sess.evidence_families.update(active_families)
        sess.reason_codes.update(reason_codes)
        sess.missing_signals_union.update(missing_signals)

    def _close_event(
        self,
        sess: _SourceSessionState,
        source_id: str,
        end_ms: int,
        confidence: float,
        close_reason: str,
    ) -> PeakCandidateEvent:
        peak_score = round(max(sess.max_hype, 0.0) * 100.0, 2)
        ev = PeakCandidateEvent(
            source_id=source_id,
            onset_ms=max(0, sess.onset_ms),
            peak_ms=max(sess.onset_ms, sess.peak_ms),
            end_ms=max(sess.onset_ms + 5000, end_ms),
            prerecord_ms=self.prerecord_ms,
            postrecord_ms=self.postrecord_ms,
            peak_score=peak_score,
            confidence=confidence,
            has_gap=sess.has_gap,
            gap_ms=sess.gap_ms,
            evidence={
                "families": sorted(sess.evidence_families),
                "reason_codes": sorted(sess.reason_codes | {close_reason}),
                "missing_signals": sorted(sess.missing_signals_union),
                "samples": sess.samples_in_event,
            },
            detector_version=self.detector_version,
        )
        sess.state = "WATCHING"
        sess.consecutive_enter_count = 0
        sess.onset_ms = 0
        sess.peak_ms = 0
        sess.max_hype = 0.0
        sess.fall_start_ms = None
        sess.has_gap = False
        sess.gap_ms = 0
        sess.samples_in_event = 0
        sess.evidence_families.clear()
        sess.reason_codes.clear()
        sess.missing_signals_union.clear()
        sess.cooldown_until_ms = end_ms + self.cooldown_ms
        return ev

    @staticmethod
    def merge_nearby_events(
        events: List[PeakCandidateEvent],
        merge_gap_ms: int = 35000,
    ) -> List[PeakCandidateEvent]:
        """Agrupa picos cercanos pertenecientes a una misma historia para evitar duplicados."""
        if len(events) <= 1:
            return events
        ordered = sorted(events, key=lambda e: e.onset_ms)
        merged: List[PeakCandidateEvent] = [ordered[0]]
        for cur in ordered[1:]:
            prev = merged[-1]
            if cur.source_id == prev.source_id and cur.onset_ms <= prev.end_ms + merge_gap_ms:
                prev.end_ms = max(prev.end_ms, cur.end_ms)
                if cur.peak_score > prev.peak_score:
                    prev.peak_score = cur.peak_score
                    prev.peak_ms = cur.peak_ms
                prev.confidence = round(max(prev.confidence, cur.confidence), 3)
                prev.has_gap = prev.has_gap or cur.has_gap
                prev.gap_ms += cur.gap_ms
            else:
                merged.append(cur)
        return merged
