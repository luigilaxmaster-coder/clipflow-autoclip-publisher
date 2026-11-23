"""Programación de publicaciones con soporte de zona horaria America/Santo_Domingo y almacenamiento UTC."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytz
from sqlalchemy.orm import Session

from clipflow.config import get_settings
from clipflow.models import PublishJob, utc_now

logger = logging.getLogger(__name__)


def get_local_timezone() -> pytz.BaseTzInfo:
    tz_name = get_settings().app.timezone or "America/Santo_Domingo"
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("America/Santo_Domingo")


def parse_schedule_to_utc(date_str: str, tz_name: Optional[str] = None) -> datetime:
    """Convierte una cadena de fecha/hora en la zona local a datetime UTC consciente."""
    tz = pytz.timezone(tz_name or get_settings().app.timezone or "America/Santo_Domingo")

    # Intentar parsear ISO con offset o fecha simple
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        # Fecha naïve: localizar en la zona horaria objetivo
        localized = tz.localize(dt)
        return localized.astimezone(timezone.utc)
    except Exception as e:
        # Intentar formatos comunes
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str, fmt)
                localized = tz.localize(dt)
                return localized.astimezone(timezone.utc)
            except ValueError:
                continue
        raise ValueError(f"No se pudo interpretar el formato de fecha: {date_str}") from e


# Mejores franjas horarias locales (America/Santo_Domingo) para Shorts/Reels/TikTok
PRIME_SLOTS_LOCAL = [
    (12, 30),  # Mediodía
    (15, 30),  # Tarde
    (18, 30),  # Prime time inicio
    (19, 30),  # Prime time pico
    (20, 30),  # Prime time noche
    (21, 30),  # Cierre nocturno
]


def build_wave_schedule(
    total_clips: int,
    remaining_today: int = 6,
    daily_limit: int = 6,
    now_utc: Optional[datetime] = None,
    tz_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Divide una tanda de clips en 2 olas inteligentes respetando la cuota diaria de la cuenta:
    - Ola 1 (Inmediata): publica ahora la mitad posible del cupo disponible hoy.
    - Ola 2 (Horario Pico): programa la otra mitad en las mejores franjas horarias locales
      (18:30, 19:30, 20:30, etc.) y, si el lote supera el cupo de hoy, pasa el resto al día siguiente.
    """
    if total_clips <= 0:
        return []

    tz = pytz.timezone(tz_name or get_settings().app.timezone or "America/Santo_Domingo")
    ref_utc = now_utc or utc_now()
    if ref_utc.tzinfo is None:
        ref_utc = ref_utc.replace(tzinfo=timezone.utc)
    now_local = ref_utc.astimezone(tz)

    avail_today = max(0, remaining_today)
    today_cap = min(total_clips, avail_today)
    wave1_count = math.ceil(today_cap / 2) if today_cap > 0 else 0
    wave2_today_count = max(0, today_cap - wave1_count)

    # Construir franjas futuras disponibles empezando por horario pico (18:30+) si es de mañana, o todas futuras +45m
    min_future = now_local + timedelta(minutes=45)
    evening_first = [(18, 30), (19, 30), (20, 30), (21, 30), (12, 30), (15, 30)]

    def _slots_for_day(day_date, ordered_hm):
        slots = []
        for h, m in ordered_hm:
            cand = tz.localize(datetime(day_date.year, day_date.month, day_date.day, h, m, 0))
            if cand >= min_future:
                slots.append(cand)
        return slots

    today_slots = _slots_for_day(now_local.date(), evening_first)
    plan: List[Dict[str, Any]] = []

    # 1. Ola inmediata (mitad de hoy)
    for idx in range(wave1_count):
        plan.append({
            "index": idx,
            "wave": 1,
            "mode": "immediate",
            "status": "QUEUED",
            "scheduled_at_utc": None,
            "scheduled_local": now_local.strftime("%Y-%m-%d %H:%M"),
            "label": "Ola 1 (Inmediata)",
        })

    # 2. Ola horario pico de hoy (segunda mitad de hoy)
    slot_cursor = 0
    day_offset = 0
    for offset_idx in range(wave2_today_count):
        if slot_cursor < len(today_slots):
            dt_local = today_slots[slot_cursor]
            slot_cursor += 1
        else:
            day_offset += 1
            next_day = now_local.date() + timedelta(days=day_offset)
            day_slots = _slots_for_day(next_day, PRIME_SLOTS_LOCAL)
            dt_local = day_slots[0] if day_slots else tz.localize(datetime(next_day.year, next_day.month, next_day.day, 18, 30, 0))

        plan.append({
            "index": len(plan),
            "wave": 2,
            "mode": "scheduled",
            "status": "SCHEDULED",
            "scheduled_at_utc": dt_local.astimezone(timezone.utc),
            "scheduled_local": dt_local.strftime("%Y-%m-%d %H:%M"),
            "label": f"Ola 2 (Horario Pico {dt_local.strftime('%d/%m %H:%M')})",
        })

    # 3. Excedente más allá del cupo disponible hoy -> días siguientes respetando daily_limit
    overflow = total_clips - len(plan)
    next_day_idx = max(1, day_offset + 1)
    while overflow > 0:
        day_date = now_local.date() + timedelta(days=next_day_idx)
        day_slots = _slots_for_day(day_date, PRIME_SLOTS_LOCAL)
        take = min(overflow, max(1, daily_limit))
        for s_i in range(take):
            if s_i < len(day_slots):
                dt_local = day_slots[s_i]
            else:
                base = day_slots[-1] if day_slots else tz.localize(datetime(day_date.year, day_date.month, day_date.day, 18, 30, 0))
                dt_local = base + timedelta(minutes=45 * (s_i - len(day_slots) + 1))
            plan.append({
                "index": len(plan),
                "wave": 2 + next_day_idx,
                "mode": "scheduled",
                "status": "SCHEDULED",
                "scheduled_at_utc": dt_local.astimezone(timezone.utc),
                "scheduled_local": dt_local.strftime("%Y-%m-%d %H:%M"),
                "label": f"Día +{next_day_idx} ({dt_local.strftime('%d/%m %H:%M')})",
            })
            overflow -= 1
        next_day_idx += 1

    return plan


class SchedulerService:
    def __init__(self, session: Session):
        self.session = session

    def get_due_jobs(self) -> List[PublishJob]:
        """Obtiene los trabajos programados listos para ser despachados (scheduled_at <= now)."""
        now = utc_now()
        return (
            self.session.query(PublishJob)
            .filter(
                PublishJob.status == "QUEUED",
                PublishJob.scheduled_at_utc != None,
                PublishJob.scheduled_at_utc <= now,
            )
            .all()
        )
