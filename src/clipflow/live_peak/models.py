"""Estructuras de datos tipadas y explicables para Live Peak Intelligence."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class TelemetrySample:
    """Muestra normalizada de ventana (p. ej. 5s) con separación estricta entre None y 0."""

    source_id: str
    window_start_utc: datetime
    window_end_utc: datetime
    source_clock_ms: int
    chat_rate: Optional[float] = None
    unique_chatters: Optional[int] = None
    total_messages: Optional[int] = None
    bot_ratio: float = 0.0
    duplicate_ratio: float = 0.0
    concurrent_viewers: Optional[int] = None
    audience_delta: Optional[float] = None
    audio_energy: Optional[float] = None
    audio_laughter_or_reaction: Optional[float] = None
    visual_motion: Optional[float] = None
    scene_change: Optional[float] = None
    semantic_surprise: Optional[float] = None
    semantic_payoff_clue: Optional[float] = None
    semantic_narrative_clarity: Optional[float] = None
    semantic_emotion: Optional[float] = None
    semantic_keywords: List[str] = field(default_factory=list)
    novelty_score: Optional[float] = None
    topic_relevance: Optional[float] = None
    transcript_excerpt: str = ""
    transcript_coherent: Optional[bool] = None
    late_sample: bool = False
    gap_detected: bool = False
    gap_ms: int = 0


@dataclass
class DetectorSignalResult:
    source_clock_ms: int
    hype_score: float
    confidence: float
    active_families: List[str]
    missing_signals: List[str]
    spam_filtered: bool
    reason_codes: List[str]
    state: str
    peak_score: float


@dataclass
class PeakCandidateEvent:
    source_id: str
    onset_ms: int
    peak_ms: int
    end_ms: int
    prerecord_ms: int = 45000
    postrecord_ms: int = 30000
    peak_score: float = 0.0
    confidence: float = 0.0
    has_gap: bool = False
    gap_ms: int = 0
    evidence: Dict[str, Any] = field(default_factory=dict)
    detector_version: str = "v1.0"


@dataclass
class RubricDimensionScore:
    dimension: str
    value: float  # 0.0 - 10.0
    weight: float  # Suma 100
    weighted_contribution: float
    confidence: float
    evidence_refs: List[str]
    model_version: str
    reason: str


@dataclass
class ClipEvaluation:
    score: float  # 0 - 100
    display_rating_10: float  # 0.0 - 10.0
    confidence: float  # 0 - 1
    editorial_band: str
    review_status: str  # APPROVED | REVIEW_REQUIRED | BLOCKED | REJECTED
    reason_codes: List[str]
    breakdown: Dict[str, Dict[str, Any]]
    model_version: str


@dataclass
class PolicyDecision:
    eligible: bool
    status: str  # SHADOW | APPROVED | REVIEW_REQUIRED | BLOCKED | REJECTED | SCHEDULED
    reason: str
    editorial_band: str
    priority_score: float
    snapshot: Dict[str, Any] = field(default_factory=dict)
