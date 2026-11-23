"""Registro empírico de resultados post-publicación y calibración versionada por nicho."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.live_peak.scoring import DEFAULT_RUBRIC_WEIGHTS
from clipflow.models import ClipOutcome, LiveClipScore


class OutcomeTracker:
    """
    Separa métricas previas del directo de resultados reales del Short publicado.
    Calcula `engagement_rate` explícitamente con denominador declarado, no compara
    volúmenes brutos entre canales distintos y recalibra pesos solo con muestra mínima.
    """

    def __init__(self, db: Session):
        self.db = db

    def record_outcome(
        self,
        *,
        job_id: str,
        platform: str,
        metric_name: str,
        metric_value: Optional[float],
        source: str,
        denominator: Optional[float] = None,
        clip_score_id: Optional[str] = None,
        niche: Optional[str] = None,
        model_version: str = "rubric-v1.0",
        availability: Optional[str] = None,
    ) -> ClipOutcome:
        avail = availability or ("AVAILABLE" if metric_value is not None else "SIN_DATOS")
        row = ClipOutcome(
            job_id=job_id,
            clip_score_id=clip_score_id,
            platform=platform.lower(),
            niche=(niche or "").strip().lower() or None,
            model_version=model_version,
            metric_name=metric_name,
            metric_value=float(metric_value) if metric_value is not None else None,
            denominator=float(denominator) if denominator is not None else None,
            availability=avail,
            source=source,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return row

    @staticmethod
    def compute_engagement_rate(
        comments: float,
        likes: float,
        shares: float,
        valid_views: float,
    ) -> Optional[float]:
        """Tasa explícita `(comments + likes + shares) / valid_views` sin mezclar métricas brutas."""
        if valid_views <= 0:
            return None
        return round((max(0.0, comments) + max(0.0, likes) + max(0.0, shares)) / float(valid_views), 5)

    def summarize_by_niche(self, niche: Optional[str] = None) -> Dict[str, Any]:
        q = self.db.query(ClipOutcome).filter(ClipOutcome.availability == "AVAILABLE")
        if niche:
            q = q.filter(ClipOutcome.niche == niche.strip().lower())
        rows = q.all()

        by_metric: Dict[str, List[float]] = {}
        for r in rows:
            if r.metric_value is not None:
                by_metric.setdefault(r.metric_name, []).append(float(r.metric_value))

        summary: Dict[str, Any] = {}
        for k, vals in by_metric.items():
            summary[k] = {
                "count": len(vals),
                "avg": round(sum(vals) / len(vals), 4),
                "max": round(max(vals), 4),
            }
        return {
            "niche": niche or "all",
            "total_observations": len(rows),
            "metrics": summary,
        }

    def propose_calibrated_weights(
        self,
        *,
        niche: Optional[str] = None,
        min_samples: int = 20,
        target_model_version: str = "rubric-v1.1",
    ) -> Dict[str, Any]:
        """
        Recalibra pesos respecto a resultados reales (`retention_avg`, `stayed_to_watch`, `engagement_rate`)
        únicamente cuando existe tamaño muestral mínimo; con pocos datos mantiene pesos estables.
        """
        q = (
            self.db.query(ClipOutcome, LiveClipScore)
            .join(LiveClipScore, ClipOutcome.clip_score_id == LiveClipScore.id)
            .filter(
                ClipOutcome.availability == "AVAILABLE",
                ClipOutcome.metric_name.in_(["retention_avg", "stayed_to_watch", "engagement_rate"]),
            )
        )
        if niche:
            q = q.filter(ClipOutcome.niche == niche.strip().lower())

        pairs = q.all()
        if len(pairs) < min_samples:
            return {
                "calibrated": False,
                "reason": f"Muestra insuficiente ({len(pairs)}/{min_samples}): se mantienen pesos estables.",
                "weights": dict(DEFAULT_RUBRIC_WEIGHTS),
                "model_version": "rubric-v1.0",
            }

        # Ajuste acotado por correlación simple entre dimensión y retención/engagement
        import json

        top_half = sorted(pairs, key=lambda p: float(p[0].metric_value or 0.0), reverse=True)[: max(1, len(pairs) // 2)]
        bottom_half = sorted(pairs, key=lambda p: float(p[0].metric_value or 0.0))[: max(1, len(pairs) // 2)]

        new_weights = dict(DEFAULT_RUBRIC_WEIGHTS)
        for dim in DEFAULT_RUBRIC_WEIGHTS:
            top_vals = []
            bot_vals = []
            for _, cs in top_half:
                bd = json.loads(cs.score_breakdown_json or "{}")
                if dim in bd:
                    top_vals.append(float(bd[dim].get("value", 5.0)))
            for _, cs in bottom_half:
                bd = json.loads(cs.score_breakdown_json or "{}")
                if dim in bd:
                    bot_vals.append(float(bd[dim].get("value", 5.0)))
            if top_vals and bot_vals:
                diff = (sum(top_vals) / len(top_vals)) - (sum(bot_vals) / len(bot_vals))
                # Ajuste suave acotado +/- 3 puntos porcentuales
                delta = max(-3.0, min(3.0, diff * 0.75))
                new_weights[dim] = max(2.0, DEFAULT_RUBRIC_WEIGHTS[dim] + delta)

        total = sum(new_weights.values()) or 100.0
        normalized = {k: round((v / total) * 100.0, 2) for k, v in new_weights.items()}
        return {
            "calibrated": True,
            "samples_used": len(pairs),
            "weights": normalized,
            "model_version": target_model_version,
            "rollback_version": "rubric-v1.0",
        }
