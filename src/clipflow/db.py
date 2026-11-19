"""Gestión de base de datos y sesiones SQLAlchemy para clipflow."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from clipflow.config import get_settings
from clipflow.models import AuditEvent, Base

_engine = None
_SessionFactory = None


def get_engine(database_url: Optional[str] = None):
    global _engine
    if _engine is None or database_url is not None:
        url = database_url or get_settings().app.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, echo=False)
    return _engine


def get_session_factory(database_url: Optional[str] = None) -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None or database_url is not None:
        engine = get_engine(database_url)
        _SessionFactory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return _SessionFactory


def init_db(database_url: Optional[str] = None) -> None:
    """Crea las tablas en la base de datos si no existen."""
    engine = get_engine(database_url)
    Base.metadata.create_all(bind=engine)


def get_db(database_url: Optional[str] = None) -> Generator[Session, None, None]:
    """Generador de sesiones de base de datos para FastAPI Depends."""
    factory = get_session_factory(database_url)
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def get_db_context(database_url: Optional[str] = None) -> Generator[Session, None, None]:
    """Context manager para bloques 'with get_db_context() as db:' en CLI y servicios."""
    gen = get_db(database_url)
    session = next(gen)
    try:
        yield session
    finally:
        try:
            next(gen)
        except StopIteration:
            pass



def record_audit(
    session: Session,
    entity_type: str,
    entity_id: str,
    actor: str,
    action: str,
    before: Optional[Any] = None,
    after: Optional[Any] = None,
) -> AuditEvent:
    """Registra un evento de auditoría de forma estructurada."""
    event = AuditEvent(
        entity_type=entity_type,
        entity_id=str(entity_id),
        actor=actor,
        action=action,
        before_json=json.dumps(before, ensure_ascii=False, default=str) if before is not None else None,
        after_json=json.dumps(after, ensure_ascii=False, default=str) if after is not None else None,
    )
    session.add(event)
    return event
