"""Programación de publicaciones con soporte de zona horaria America/Santo_Domingo y almacenamiento UTC."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

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
