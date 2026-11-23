"""Programador adaptativo 24h (live_peak_adaptive) aislado por perfil, cooldown y diversidad."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from clipflow.live_peak.alignment import _ensure_utc
from clipflow.scheduler import PRIME_SLOTS_LOCAL, get_local_timezone


def build_live_peak_adaptive_slot(
    *,
    now_utc: Optional[datetime] = None,
    recent_profile_posts_utc: Optional[Iterable[datetime]] = None,
    max_posts_per_day_per_profile: int = 2,
    minimum_post_gap_minutes: int = 180,
    is_exceptional: bool = False,
    allow_immediate_urgent: bool = False,
    tz_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Calcula el próximo slot disponible para un único clip elegible de Live Peak sin usar `build_wave_schedule`.
    - Nunca lanza dos clips simultáneamente en el mismo perfil (respeta `minimum_post_gap_minutes`).
    - Respeta `max_posts_per_day_per_profile` (default 2/día/perfil).
    - Permite a un clip excepcional (>=95) ocupar inmediatamente un único slot libre urgente cuando está permitido.
    """
    ref_utc = _ensure_utc(now_utc or datetime.now(timezone.utc))
    local_tz = get_local_timezone()
    if tz_name:
        import pytz
        local_tz = pytz.timezone(tz_name)

    existing = sorted(_ensure_utc(dt) for dt in (recent_profile_posts_utc or []))
    gap_td = timedelta(minutes=max(30, int(minimum_post_gap_minutes)))
    cap_per_day = max(1, int(max_posts_per_day_per_profile))

    def _day_count(day_local_date) -> int:
        return sum(1 for dt in existing if dt.astimezone(local_tz).date() == day_local_date)

    def _respects_gap(cand_utc: datetime) -> bool:
        return all(abs((cand_utc - ex).total_seconds()) >= gap_td.total_seconds() for ex in existing)

    now_local = ref_utc.astimezone(local_tz)
    # Urgente excepcional: puede tomar slot inmediato único si respeta cuota diaria y cooldown
    if is_exceptional and allow_immediate_urgent and _day_count(now_local.date()) < cap_per_day and _respects_gap(ref_utc):
        return {
            "status": "QUEUED",
            "scheduled_at_utc": ref_utc,
            "scheduled_local": now_local.strftime("%Y-%m-%d %H:%M"),
            "slot_reason": "Slot inmediato único para candidato excepcional respetando cuota y separación.",
        }

    # Buscar próximo horario pico o slot separado en los próximos 5 días locales
    ordered_hm = sorted(PRIME_SLOTS_LOCAL)
    for day_offset in range(0, 6):
        day_date = (now_local + timedelta(days=day_offset)).date()
        if _day_count(day_date) >= cap_per_day:
            continue

        # Construir candidatos de ese día
        day_candidates: List[datetime] = []
        if is_exceptional and day_offset == 0:
            # Un clip excepcional fuera de horario óptimo (máx 1) puede salir tras cumplir gap
            after_last = ref_utc + timedelta(minutes=5)
            for ex in existing:
                if ex + gap_td > after_last:
                    after_last = ex + gap_td
            if after_last.astimezone(local_tz).date() == day_date:
                day_candidates.append(after_last)

        for h, m in ordered_hm:
            cand_local = local_tz.localize(datetime(day_date.year, day_date.month, day_date.day, h, m, 0))
            cand_utc = cand_local.astimezone(timezone.utc)
            if cand_utc >= ref_utc + timedelta(minutes=10):
                day_candidates.append(cand_utc)

        for cand_utc in sorted(day_candidates):
            if _respects_gap(cand_utc):
                cand_local = cand_utc.astimezone(local_tz)
                return {
                    "status": "SCHEDULED",
                    "scheduled_at_utc": cand_utc,
                    "scheduled_local": cand_local.strftime("%Y-%m-%d %H:%M"),
                    "slot_reason": "Programación adaptativa respetando cuota diaria y separación entre clips.",
                }

    # Si todos los días están llenos dentro de la ventana, postergar al día +6 en primera franja
    fallback_day = (now_local + timedelta(days=6)).date()
    fallback_local = local_tz.localize(datetime(fallback_day.year, fallback_day.month, fallback_day.day, 18, 30, 0))
    return {
        "status": "SCHEDULED",
        "scheduled_at_utc": fallback_local.astimezone(timezone.utc),
        "scheduled_local": fallback_local.strftime("%Y-%m-%d %H:%M"),
        "slot_reason": "Diferido por saturación previa del perfil destino.",
    }


class AdaptiveLiveScheduler:
    """Ordena candidatos elegibles por prioridad y asigna slots sin saturar perfiles ni repetir eventos."""

    def __init__(
        self,
        max_posts_per_day_per_profile: int = 2,
        minimum_post_gap_minutes: int = 180,
    ):
        self.max_posts_per_day_per_profile = max(1, int(max_posts_per_day_per_profile))
        self.minimum_post_gap_minutes = max(30, int(minimum_post_gap_minutes))

    def schedule_ranked_candidates(
        self,
        candidates: List[Dict[str, Any]],
        *,
        existing_posts_by_target: Optional[Dict[Tuple[str, str], List[datetime]]] = None,
        now_utc: Optional[datetime] = None,
        allow_immediate_urgent: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Cada candidato espera: `clip_id`, `event_id`, `destination_connection_id`, `platform`,
        `priority_score`, `score`, `eligible`, opcionalmente `topic_key`.
        """
        ref = _ensure_utc(now_utc or datetime.now(timezone.utc))
        target_history: Dict[Tuple[str, str], List[datetime]] = {
            k: [_ensure_utc(dt) for dt in v] for k, v in (existing_posts_by_target or {}).items()
        }
        seen_events_per_target: Dict[Tuple[str, str], set[str]] = {}
        seen_last_topic_per_target: Dict[Tuple[str, str], str] = {}

        ordered = sorted(
            [c for c in candidates if c.get("eligible")],
            key=lambda x: (float(x.get("priority_score", 0.0)), float(x.get("score", 0.0))),
            reverse=True,
        )

        decisions: List[Dict[str, Any]] = []
        for cand in ordered:
            target = (str(cand.get("destination_connection_id") or "default"), str(cand.get("platform") or "youtube"))
            ev_id = str(cand.get("event_id") or "")
            topic_key = str(cand.get("topic_key") or "").strip().lower()

            ev_set = seen_events_per_target.setdefault(target, set())
            if ev_id and ev_id in ev_set:
                decisions.append({
                    **cand,
                    "scheduled": False,
                    "status": "SKIPPED_DIVERSITY_EVENT",
                    "reason": "Límite de diversidad: ya existe un clip programado de este acontecimiento para este perfil.",
                })
                continue

            if topic_key and seen_last_topic_per_target.get(target) == topic_key:
                decisions.append({
                    **cand,
                    "scheduled": False,
                    "status": "SKIPPED_DIVERSITY_TOPIC",
                    "reason": "Evita repetición consecutiva del mismo tema en este perfil.",
                })
                continue

            hist = target_history.setdefault(target, [])
            slot = build_live_peak_adaptive_slot(
                now_utc=ref,
                recent_profile_posts_utc=hist,
                max_posts_per_day_per_profile=self.max_posts_per_day_per_profile,
                minimum_post_gap_minutes=self.minimum_post_gap_minutes,
                is_exceptional=float(cand.get("score", 0.0)) >= 95.0,
                allow_immediate_urgent=allow_immediate_urgent,
            )
            hist.append(slot["scheduled_at_utc"])
            if ev_id:
                ev_set.add(ev_id)
            if topic_key:
                seen_last_topic_per_target[target] = topic_key

            decisions.append({
                **cand,
                "scheduled": True,
                **slot,
            })

        return decisions
