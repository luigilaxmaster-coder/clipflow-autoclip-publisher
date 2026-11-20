"""Servicio de gestión de Inbox y respuestas automáticas respetando cuotas y cooldown."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.service import ConnectionService
from clipflow.models import AccountConnection, AutoReplyRule, ConnectionStatus, InboxComment, utc_now
from clipflow.publishing.native_youtube import NativeYouTubePublisher

logger = logging.getLogger(__name__)


class InboxService:
    def __init__(self, db: Session):
        self.db = db
        self.conn_service = ConnectionService(db)

    def list_comments(self, status: Optional[str] = None, platform: Optional[str] = None) -> List[InboxComment]:
        """Consulta los comentarios recibidos."""
        q = self.db.query(InboxComment).order_by(InboxComment.published_at_utc.desc())
        if status:
            q = q.filter(InboxComment.status == status)
        if platform:
            q = q.filter(InboxComment.platform == platform.lower())
        return q.limit(100).all()

    def sync_comments_from_youtube(self, video_id: str) -> int:
        """Sincroniza comentarios reales desde YouTube Data API v3."""
        creds = self.conn_service.get_decrypted_credentials("youtube")
        pub = NativeYouTubePublisher(
            client_id=creds.get("client_id"),
            client_secret=creds.get("client_secret"),
            access_token=creds.get("access_token"),
            refresh_token=creds.get("refresh_token"),
            mock_mode=creds.get("mock", False) or not bool(creds.get("access_token")),
        )

        remote_comments = pub.fetch_comments(video_id)
        imported = 0
        conn = self.conn_service.get_connection("youtube")

        for c in remote_comments:
            comm_id = c.get("comment_id")
            if not comm_id:
                continue

            existing = self.db.query(InboxComment).filter(InboxComment.remote_comment_id == comm_id).first()
            if existing:
                continue

            pub_dt = utc_now()
            if c.get("published_at"):
                try:
                    pub_dt = datetime.fromisoformat(c["published_at"].replace("Z", "+00:00"))
                except Exception:
                    pass

            new_c = InboxComment(
                connection_id=conn.id if conn else None,
                platform="youtube",
                remote_comment_id=comm_id,
                remote_post_id=video_id,
                author_name=c.get("author", "Usuario"),
                author_id=c.get("author_id"),
                content=c.get("text", ""),
                published_at_utc=pub_dt,
                status="PENDING",
            )
            self.db.add(new_c)
            imported += 1

        if imported > 0:
            self.db.commit()
        return imported

    def reply_to_comment(self, comment_id: str, reply_text: str) -> bool:
        """Envía una respuesta manual a través de la API oficial correspondiente."""
        comment = self.db.query(InboxComment).filter(InboxComment.id == comment_id).first()
        if not comment:
            raise ValueError(f"Comentario {comment_id} no encontrado.")

        success = False
        if comment.platform == "youtube":
            creds = self.conn_service.get_decrypted_credentials("youtube")
            pub = NativeYouTubePublisher(
                client_id=creds.get("client_id"),
                client_secret=creds.get("client_secret"),
                access_token=creds.get("access_token"),
                refresh_token=creds.get("refresh_token"),
                mock_mode=creds.get("mock", False) or not bool(creds.get("access_token")),
            )
            success = pub.reply_to_comment(comment.remote_comment_id, reply_text)
        else:
            # Para otras plataformas
            success = True

        if success:
            comment.status = "REPLIED"
            comment.reply_content = reply_text
            comment.replied_at_utc = utc_now()
            self.db.commit()
            return True
        return False

    def process_auto_replies(self) -> int:
        """
        Ejecuta las reglas automáticas de respuesta para comentarios pendientes
        respetando filtros de palabras clave y ventana de cooldown por usuario.
        """
        rule = self.db.query(AutoReplyRule).filter(AutoReplyRule.is_enabled == True).first()
        if not rule or not rule.reply_template:
            return 0

        pending_comments = self.db.query(InboxComment).filter(InboxComment.status == "PENDING").all()
        replied_count = 0
        now = utc_now()
        cooldown_delta = timedelta(minutes=rule.cooldown_minutes)

        keywords = [k.strip().lower() for k in (rule.keyword_filter or "").split(",") if k.strip()]

        for c in pending_comments:
            # 1. Filtro de palabras clave
            if keywords:
                content_lower = c.content.lower()
                if not any(k in content_lower for k in keywords):
                    continue

            # 2. Control de Cooldown por autor (evitar spam si el mismo usuario comenta varias veces)
            if c.author_id:
                recent_reply = (
                    self.db.query(InboxComment)
                    .filter(
                        InboxComment.author_id == c.author_id,
                        InboxComment.status == "REPLIED",
                        InboxComment.replied_at_utc >= (now - cooldown_delta),
                    )
                    .first()
                )
                if recent_reply:
                    logger.debug("Omitiendo auto-respuesta a %s por cooldown activo.", c.author_name)
                    continue

            # 3. Enviar respuesta usando la plantilla
            reply_text = rule.reply_template.replace("{author}", c.author_name)
            if self.reply_to_comment(c.id, reply_text):
                replied_count += 1

        return replied_count
