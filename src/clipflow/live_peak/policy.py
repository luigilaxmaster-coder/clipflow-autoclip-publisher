"""Motor de políticas, derechos, deduplicación y compuertas de seguridad Live Peak."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

from clipflow.config import LivePeakConfig
from clipflow.live_peak.models import PolicyDecision
from clipflow.live_peak.scoring import classify_editorial_band


class LivePeakPolicyEngine:
    """
    Aplica compuertas obligatorias previas a cualquier programación o autopublicación:
    - Derechos confirmados + dueño aprobado
    - MediaQA aprobado y sin fallo técnico
    - score >= min_auto_score y confidence >= min_auto_confidence
    - Deduplicación por hash/evento/contenido publicado
    - Separación estricta entre `shadow`, `review` y `auto` (+ `publish_publicly` por perfil)
    """

    def __init__(self, policy: Optional[LivePeakConfig] = None):
        self.policy = policy or LivePeakConfig()

    def evaluate_gate(
        self,
        *,
        score: float,
        confidence: float,
        media_qa_passed: bool,
        rights_status: str,
        owner_approved: bool,
        account_authorized: bool,
        publish_publicly_allowed: bool,
        mode: Optional[str] = None,
        content_hash: Optional[str] = None,
        existing_hashes: Optional[Set[str]] = None,
        transcript_coherent: bool = True,
        freshness_bonus: float = 0.0,
        audience_fit_bonus: float = 0.0,
        duplication_penalty: float = 0.0,
        saturation_penalty: float = 0.0,
    ) -> PolicyDecision:
        active_mode = (mode or self.policy.mode or "shadow").lower()
        band = classify_editorial_band(score)

        # Bonos/penalizaciones acotados para que la calidad editorial mande siempre
        bounded_fresh = max(-5.0, min(6.0, float(freshness_bonus)))
        bounded_fit = max(-5.0, min(5.0, float(audience_fit_bonus)))
        bounded_dup = max(0.0, min(25.0, float(duplication_penalty)))
        bounded_sat = max(0.0, min(20.0, float(saturation_penalty)))
        priority_score = round(score + bounded_fresh + bounded_fit - bounded_dup - bounded_sat, 2)

        snapshot: Dict[str, Any] = {
            "mode": active_mode,
            "min_auto_score": self.policy.min_auto_score,
            "min_auto_confidence": self.policy.min_auto_confidence,
            "publish_publicly_allowed": bool(publish_publicly_allowed and self.policy.publish_publicly),
            "rights_status": rights_status,
            "owner_approved": owner_approved,
            "media_qa_passed": media_qa_passed,
            "account_authorized": account_authorized,
        }

        if rights_status != self.policy.min_rights_status or not owner_approved:
            return PolicyDecision(
                eligible=False,
                status="BLOCKED",
                reason="Bloqueado: derechos de transmisión no confirmados o sin aprobación del propietario.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if not media_qa_passed or not transcript_coherent:
            return PolicyDecision(
                eligible=False,
                status="BLOCKED",
                reason="Bloqueado: falla verificación técnica MediaQA o coherencia de transcripción.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if content_hash and existing_hashes and content_hash in existing_hashes:
            return PolicyDecision(
                eligible=False,
                status="REJECTED",
                reason="Descartado: fragmento indistinguible o ya registrado previamente.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if confidence < self.policy.min_auto_confidence:
            return PolicyDecision(
                eligible=False,
                status="REVIEW_REQUIRED",
                reason=(
                    f"Revisión requerida: confianza {confidence:.2f} por debajo del umbral "
                    f"({self.policy.min_auto_confidence:.2f})."
                ),
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if score < self.policy.min_auto_score:
            status = "REVIEW_REQUIRED" if score >= 70.0 else "REJECTED"
            return PolicyDecision(
                eligible=False,
                status=status,
                reason=f"Score {score:.1f}/100 inferior al mínimo automático ({self.policy.min_auto_score:.0f}/100).",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        # Cumple score >= 90, confianza >= 0.75, QA y derechos
        if active_mode == "shadow":
            return PolicyDecision(
                eligible=False,
                status="SHADOW",
                reason="Modo sombra activo: scorecard y candidato guardados sin despachar publicación.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if not account_authorized:
            return PolicyDecision(
                eligible=False,
                status="REVIEW_REQUIRED",
                reason="Candidato excelente, pero el perfil destino no tiene autorización OAuth vigente.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if active_mode == "review":
            return PolicyDecision(
                eligible=False,
                status="REVIEW_REQUIRED",
                reason="Modo revisión activo: esperando aprobación explícita antes de agendar.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        if not (publish_publicly_allowed and self.policy.publish_publicly):
            # Modo auto pero sin autorización explícita de publicación pública -> solo borrador/privado revisable
            return PolicyDecision(
                eligible=True,
                status="REVIEW_REQUIRED",
                reason="Autopublicación pública desactivada para este perfil: requiere aprobación manual o envío privado.",
                editorial_band=band,
                priority_score=priority_score,
                snapshot=snapshot,
            )

        return PolicyDecision(
            eligible=True,
            status="APPROVED",
            reason=f"Elegible para cola adaptativa ({band}: {score:.1f}/100, conf {confidence:.2f}).",
            editorial_band=band,
            priority_score=priority_score,
            snapshot=snapshot,
        )
