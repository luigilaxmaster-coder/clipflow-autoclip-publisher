"""Evaluador editorial interpretable en 8 dimensiones (rúbrica 0-100 + confianza 0-1)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from clipflow.live_peak.models import ClipEvaluation, RubricDimensionScore

DEFAULT_RUBRIC_WEIGHTS: Dict[str, float] = {
    "hook": 20.0,              # Hook / retención temprana (1-3s)
    "payoff": 20.0,            # Payoff / resolución
    "narrative_context": 15.0, # Autonomía narrativa / contexto
    "emotion": 12.0,           # Respuesta emocional legítima
    "novelty": 10.0,           # Originalidad / no repetición
    "technical_quality": 8.0,  # Calidad audiovisual y subtítulos
    "social_evidence": 8.0,    # Evidencia social contextual verificada
    "niche_fit": 7.0,          # Afinidad con el canal destino
}


def classify_editorial_band(score: float) -> str:
    if score >= 95.0:
        return "Excepcional"
    if score >= 90.0:
        return "Excelente"
    if score >= 85.0:
        return "Muy bueno"
    if score >= 70.0:
        return "Archivo"
    return "Descartar"


class EngagementScorer:
    """
    Evalúa cada candidato en 8 dimensiones (nota 0-10 por dimensión, ponderado a 100)
    separando estrictamente `peak_score` de `clip_score` y `confidence`.
    Un chat lleno de 'LOL' sin contexto/payoff o un clip técnicamente defectuoso no obtiene >=90.
    """

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        model_version: str = "rubric-v1.0",
        min_auto_score: float = 90.0,
        min_auto_confidence: float = 0.75,
    ):
        raw_weights = dict(DEFAULT_RUBRIC_WEIGHTS)
        if weights:
            for k, v in weights.items():
                if k in raw_weights:
                    raw_weights[k] = max(0.0, float(v))
        total_w = sum(raw_weights.values()) or 100.0
        self.weights: Dict[str, float] = {
            k: round((v / total_w) * 100.0, 4) for k, v in raw_weights.items()
        }
        self.model_version = model_version
        self.min_auto_score = float(min_auto_score)
        self.min_auto_confidence = float(min_auto_confidence)

    @staticmethod
    def _clamp10(val: Optional[float], default: float = 5.0) -> float:
        if val is None:
            return default
        return round(max(0.0, min(10.0, float(val))), 2)

    def evaluate(
        self,
        dimensions: Dict[str, Any],
        *,
        missing_signals: Optional[List[str]] = None,
        has_gap: bool = False,
        transcript_coherent: Optional[bool] = True,
        spam_chat_only: bool = False,
        media_qa_passed: bool = True,
        rights_confirmed: bool = True,
        model_version: Optional[str] = None,
    ) -> ClipEvaluation:
        ver = model_version or self.model_version
        missing = list(missing_signals or [])
        reason_codes: List[str] = []

        # Extracción normalizada (0-10) por dimensión con penalización explícita si es ruido sin historia
        hook_val = self._clamp10(dimensions.get("hook"), default=5.0)
        payoff_val = self._clamp10(dimensions.get("payoff"), default=5.0)
        context_val = self._clamp10(dimensions.get("narrative_context"), default=5.0)
        emotion_val = self._clamp10(dimensions.get("emotion"), default=5.0)
        novelty_val = self._clamp10(dimensions.get("novelty"), default=6.0)
        tech_val = self._clamp10(dimensions.get("technical_quality"), default=8.0 if media_qa_passed else 2.0)
        social_val = self._clamp10(dimensions.get("social_evidence"), default=5.0)
        niche_val = self._clamp10(dimensions.get("niche_fit"), default=6.0)

        if spam_chat_only:
            # Spam/"LOL" masivo no otorga mérito social ni sustituye payoff/contexto
            social_val = min(social_val, 3.0)
            payoff_val = min(payoff_val, 4.5)
            context_val = min(context_val, 4.5)
            reason_codes.append("NOISE_WITHOUT_PAYOFF")

        if transcript_coherent is False:
            context_val = min(context_val, 3.0)
            reason_codes.append("INCOHERENT_TRANSCRIPT")

        if not media_qa_passed:
            tech_val = min(tech_val, 2.5)
            reason_codes.append("MEDIA_QA_FAILED")

        values_map = {
            "hook": hook_val,
            "payoff": payoff_val,
            "narrative_context": context_val,
            "emotion": emotion_val,
            "novelty": novelty_val,
            "technical_quality": tech_val,
            "social_evidence": social_val,
            "niche_fit": niche_val,
        }

        evidence_map: Dict[str, List[str]] = dimensions.get("evidence_refs") or {}
        reasons_map: Dict[str, str] = dimensions.get("reasons") or {}

        # Confianza (0-1): empieza en 0.98 y se descuenta por señales ausentes, incoherencia o gap
        explicit_conf = dimensions.get("confidence")
        if explicit_conf is not None:
            confidence = max(0.0, min(1.0, float(explicit_conf)))
        else:
            penalty = 0.08 * len(set(missing))
            if has_gap:
                penalty += 0.12
            if transcript_coherent is False:
                penalty += 0.20
            confidence = max(0.20, min(0.98, 0.96 - penalty))

        if "chat" in missing:
            reason_codes.append("NO_CHAT_SIGNAL")
        if "audience" in missing:
            reason_codes.append("NO_VIEWERS_SIGNAL")
        if has_gap:
            reason_codes.append("CAPTURE_GAP_PRESENT")

        breakdown: Dict[str, Dict[str, Any]] = {}
        total_score = 0.0
        for dim, weight in self.weights.items():
            val = values_map[dim]
            contrib = round((weight / 10.0) * val, 3)
            total_score += contrib
            dim_score = RubricDimensionScore(
                dimension=dim,
                value=val,
                weight=round(weight, 2),
                weighted_contribution=contrib,
                confidence=round(confidence, 3),
                evidence_refs=list(evidence_map.get(dim) or []),
                model_version=ver,
                reason=str(reasons_map.get(dim) or f"{dim}={val}/10"),
            )
            breakdown[dim] = {
                "value": dim_score.value,
                "weight": dim_score.weight,
                "weighted_contribution": dim_score.weighted_contribution,
                "confidence": dim_score.confidence,
                "evidence_refs": dim_score.evidence_refs,
                "model_version": dim_score.model_version,
                "reason": dim_score.reason,
            }

        total_score = round(max(0.0, min(100.0, total_score)), 2)
        band = classify_editorial_band(total_score)

        # Códigos interpretables por dimensión fuerte o débil
        if hook_val >= 8.5:
            reason_codes.append("STRONG_HOOK")
        elif hook_val < 5.5:
            reason_codes.append("WEAK_HOOK")
        if payoff_val >= 8.5:
            reason_codes.append("PAYOFF_COMPLETE")
        elif payoff_val < 5.0:
            reason_codes.append("WEAK_PAYOFF")
        if context_val >= 8.0:
            reason_codes.append("SELF_CONTAINED_CONTEXT")
        if social_val >= 8.0:
            reason_codes.append("SOCIAL_EVIDENCE_CONFIRMED")

        if not rights_confirmed or not media_qa_passed:
            review_status = "BLOCKED"
            if not rights_confirmed:
                reason_codes.append("RIGHTS_NOT_CONFIRMED")
        elif confidence < self.min_auto_confidence:
            review_status = "REVIEW_REQUIRED"
            reason_codes.append("LOW_CONFIDENCE")
        elif total_score >= self.min_auto_score:
            review_status = "APPROVED"
        elif total_score >= 70.0:
            review_status = "REVIEW_REQUIRED"
        else:
            review_status = "REJECTED"

        return ClipEvaluation(
            score=total_score,
            display_rating_10=round(total_score / 10.0, 1),
            confidence=round(confidence, 3),
            editorial_band=band,
            review_status=review_status,
            reason_codes=sorted(set(reason_codes)),
            breakdown=breakdown,
            model_version=ver,
        )
