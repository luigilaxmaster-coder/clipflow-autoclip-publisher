"""Pruebas del inbox de comentarios y motor de respuestas automáticas con cooldown."""
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session

from clipflow.inbox.service import InboxService
from clipflow.models import AutoReplyRule, InboxComment, utc_now


def test_inbox_sync_and_manual_reply(db_session: Session):
    service = InboxService(db_session)

    # 1. Sincronizar comentarios en modo mock
    count = service.sync_comments_from_youtube("mock_video_id_123")
    assert count >= 1

    comments = service.list_comments(status="PENDING")
    assert len(comments) >= 1
    target_c = comments[0]

    # 2. Responder manualmente
    ok = service.reply_to_comment(target_c.id, "¡Muchas gracias por ver el clip!")
    assert ok is True

    db_session.refresh(target_c)
    assert target_c.status == "REPLIED"
    assert "Muchas gracias" in target_c.reply_content


def test_auto_reply_cooldown_and_filter(db_session: Session):
    service = InboxService(db_session)

    # 1. Crear regla de auto-respuesta activa con filtro de palabra clave
    rule = AutoReplyRule(
        is_enabled=True,
        keyword_filter="genial,excelente,interesante",
        cooldown_minutes=60,
        reply_template="¡Hola {author}, muchas gracias por el feedback!",
    )
    db_session.add(rule)

    # 2. Comentario que coincide con la palabra clave
    c_match = InboxComment(
        platform="youtube",
        remote_comment_id="comm-match-1",
        author_name="CarlosTech",
        author_id="user-carlos-1",
        content="¡Excelente video, muy bien explicado!",
        status="PENDING",
    )
    # 3. Comentario que NO coincide con la palabra clave
    c_no_match = InboxComment(
        platform="youtube",
        remote_comment_id="comm-nomatch-2",
        author_name="PedroGamer",
        author_id="user-pedro-2",
        content="Cualquier texto al azar...",
        status="PENDING",
    )
    db_session.add_all([c_match, c_no_match])
    db_session.commit()

    # 4. Procesar auto-respuestas
    replied = service.process_auto_replies()
    assert replied == 1

    db_session.refresh(c_match)
    db_session.refresh(c_no_match)
    assert c_match.status == "REPLIED"
    assert "CarlosTech" in c_match.reply_content
    assert c_no_match.status == "PENDING"

    # 5. Comprobar Cooldown: Si CarlosTech vuelve a comentar dentro de la hora, NO debe responderse
    c_carlos_spam = InboxComment(
        platform="youtube",
        remote_comment_id="comm-spam-3",
        author_name="CarlosTech",
        author_id="user-carlos-1",
        content="¡Excelente de nuevo!",
        status="PENDING",
    )
    db_session.add(c_carlos_spam)
    db_session.commit()

    replied_second = service.process_auto_replies()
    assert replied_second == 0
    db_session.refresh(c_carlos_spam)
    assert c_carlos_spam.status == "PENDING"
