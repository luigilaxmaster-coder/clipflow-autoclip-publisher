"""Servicio de gestión multi-perfil, OAuth 2.0, enrutamiento por nicho y diagnóstico de conexiones."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.crypto import decrypt_credentials, encrypt_credentials
from clipflow.connections.providers import ALL_PROVIDERS, get_provider
from clipflow.models import (
    AccountConnection,
    ConnectionStatus,
    JobStatus,
    OAuthState,
    PostDraft,
    PublishJob,
    utc_now,
)
from clipflow.security import is_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)


def _normalize_text(text: str) -> str:
    """Normaliza acentos y minúsculas para emparejamiento de nichos."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    ascii_txt = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_txt.lower()


def _parse_niche_tags(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [t.strip() for t in re.split(r"[,;\n]+", raw) if t.strip()]


class ConnectionService:
    def __init__(self, db: Session):
        self.db = db

    def count_uploads_last_24h(self, conn: AccountConnection) -> int:
        """Cuenta subidas reales de los últimos 24h atribuidas a esta cuenta/canal."""
        since = utc_now() - timedelta(hours=24)
        total_for_provider = (
            self.db.query(AccountConnection)
            .filter(AccountConnection.provider_id == conn.provider_id)
            .count()
        )
        jobs = (
            self.db.query(PublishJob)
            .join(PostDraft, PublishJob.post_draft_id == PostDraft.id)
            .filter(
                PostDraft.platform == conn.provider_id,
                PublishJob.status.in_(
                    [JobStatus.PUBLISHED, JobStatus.PROCESSING, "REMOTE_PROCESSING", JobStatus.UPLOADING]
                ),
                PublishJob.updated_at >= since,
            )
            .all()
        )
        count = 0
        refs = {r for r in (conn.id, conn.account_id, conn.account_name) if r}
        for j in jobs:
            draft_acc = j.post_draft.account_id if j.post_draft else None
            if j.remote_channel_id and conn.account_id and j.remote_channel_id == conn.account_id:
                count += 1
            elif draft_acc and draft_acc in refs:
                count += 1
            elif total_for_provider == 1 and not j.remote_channel_id and draft_acc in (None, "", "default", "main"):
                count += 1
        return count

    def _serialize_account(self, db_conn: AccountConnection) -> Dict[str, Any]:
        provider = get_provider(db_conn.provider_id)
        info = provider.info if provider else None
        creds = decrypt_credentials(db_conn.encrypted_credentials) if db_conn.encrypted_credentials else {}
        has_oauth_client = bool(creds.get("client_id") and creds.get("client_secret")) or bool(
            creds.get("client_key") and creds.get("client_secret")
        )
        if db_conn.provider_id == "youtube" and not has_oauth_client:
            has_oauth_client = bool(
                (os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
                and (os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()
            )
        elif db_conn.provider_id == "tiktok" and not has_oauth_client:
            has_oauth_client = bool(
                (os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
                and (os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()
            )

        limit = max(1, int(db_conn.daily_upload_limit or 6))
        used_today = self.count_uploads_last_24h(db_conn)
        niche_list = _parse_niche_tags(db_conn.niche_tags)
        return {
            "connection_id": db_conn.id,
            "provider_id": db_conn.provider_id,
            "provider_name": info.name if info else db_conn.provider_id.title(),
            "auth_type": info.auth_type if info else "oauth2",
            "required_fields": info.required_fields if info else [],
            "status": db_conn.status,
            "account_name": db_conn.account_name,
            "account_id": db_conn.account_id,
            "niche_tags": db_conn.niche_tags or "",
            "niche_list": niche_list,
            "daily_upload_limit": limit,
            "used_today": used_today,
            "remaining_today": max(0, limit - used_today),
            "is_default": bool(db_conn.is_default),
            "last_checked_at": db_conn.last_checked_at.isoformat() if db_conn.last_checked_at else None,
            "last_error": db_conn.last_error,
            "expires_at": db_conn.expires_at.isoformat() if db_conn.expires_at else None,
            "has_oauth_client": has_oauth_client,
            "has_refresh_token": bool(creds.get("refresh_token")),
            "scopes": json.loads(db_conn.scopes_json) if db_conn.scopes_json else [],
        }

    def list_provider_accounts(self, provider_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista todas las tarjetas/cuentas individuales registradas (multi-canal)."""
        q = self.db.query(AccountConnection)
        if provider_id:
            q = q.filter(AccountConnection.provider_id == provider_id.lower())
        rows = q.order_by(AccountConnection.provider_id.asc(), AccountConnection.is_default.desc(), AccountConnection.created_at.asc()).all()
        return [self._serialize_account(r) for r in rows]

    def list_connections(self) -> List[Dict[str, Any]]:
        """
        Retorna los 18 proveedores integrados con el resumen principal y todas
        sus cuentas/perfiles asociados en 'accounts'.
        """
        all_rows = self.db.query(AccountConnection).order_by(
            AccountConnection.is_default.desc(),
            AccountConnection.created_at.asc(),
        ).all()
        grouped: Dict[str, List[AccountConnection]] = {}
        for row in all_rows:
            grouped.setdefault(row.provider_id, []).append(row)

        result = []
        for p in ALL_PROVIDERS:
            info = p.info
            rows = grouped.get(info.id, [])
            primary = next((r for r in rows if r.status == ConnectionStatus.CONNECTED), rows[0] if rows else None)
            accounts_serialized = [self._serialize_account(r) for r in rows]

            if primary:
                p_ser = self._serialize_account(primary)
                status = p_ser["status"]
                account_name = p_ser["account_name"]
                account_id = p_ser["account_id"]
                last_checked = p_ser["last_checked_at"]
                last_error = p_ser["last_error"]
                expires_at = p_ser["expires_at"]
                conn_id = p_ser["connection_id"]
                has_oauth_client = p_ser["has_oauth_client"]
                has_refresh_token = p_ser["has_refresh_token"]
                scopes = p_ser["scopes"]
            else:
                has_oauth_client = bool(
                    (
                        info.id == "youtube"
                        and (os.getenv("YOUTUBE_CLIENT_ID") or "").strip()
                        and (os.getenv("YOUTUBE_CLIENT_SECRET") or "").strip()
                    )
                    or (
                        info.id == "tiktok"
                        and (os.getenv("TIKTOK_CLIENT_KEY") or "").strip()
                        and (os.getenv("TIKTOK_CLIENT_SECRET") or "").strip()
                    )
                )
                status = ConnectionStatus.NEEDS_APPROVAL if has_oauth_client else ConnectionStatus.NEEDS_CONFIG
                account_name = None
                account_id = None
                last_checked = None
                last_error = None
                expires_at = None
                conn_id = None
                has_refresh_token = False
                scopes = []

            result.append({
                "id": info.id,
                "name": info.name,
                "kind": info.kind,
                "auth_type": info.auth_type,
                "description": info.description,
                "known_limits": info.known_limits,
                "required_fields": info.required_fields,
                "capabilities": info.capabilities,
                "doc_url": info.doc_url,
                "status": status,
                "account_name": account_name,
                "account_id": account_id,
                "connection_id": conn_id,
                "last_checked_at": last_checked,
                "last_error": last_error,
                "expires_at": expires_at,
                "has_oauth_client": has_oauth_client,
                "has_refresh_token": has_refresh_token,
                "scopes": scopes,
                "accounts": accounts_serialized,
                "accounts_count": len(accounts_serialized),
            })

        return result

    def get_connection_by_id(self, connection_id: str) -> Optional[AccountConnection]:
        if not connection_id:
            return None
        return self.db.query(AccountConnection).filter(AccountConnection.id == connection_id).first()

    def get_connection(self, provider_id: str, account_ref: Optional[str] = None) -> Optional[AccountConnection]:
        """
        Resuelve una conexión para provider_id.
        Si se pasa account_ref (connection_id, account_id o account_name), devuelve esa tarjeta exacta.
        """
        pid = provider_id.lower()
        rows = (
            self.db.query(AccountConnection)
            .filter(AccountConnection.provider_id == pid)
            .order_by(AccountConnection.is_default.desc(), AccountConnection.created_at.asc())
            .all()
        )
        if not rows:
            return None
        if account_ref:
            ref_clean = account_ref.strip()
            for r in rows:
                if r.id == ref_clean or (r.account_id and r.account_id == ref_clean) or r.account_name == ref_clean:
                    return r
        connected = [r for r in rows if r.status == ConnectionStatus.CONNECTED]
        return connected[0] if connected else rows[0]

    def get_decrypted_credentials(self, provider_id: str, account_ref: Optional[str] = None) -> Dict[str, Any]:
        conn = self.get_connection(provider_id, account_ref=account_ref)
        if not conn or not conn.encrypted_credentials:
            return {}
        return decrypt_credentials(conn.encrypted_credentials)

    def resolve_account_by_niche(self, provider_id: str, context_text: str) -> Optional[Dict[str, Any]]:
        """
        Identifica el canal/tarjeta más adecuado para un texto/título/link según los 'niche_tags'
        configurados en cada perfil conectado de ese proveedor.
        No evade cuotas ni salta de canal por falta de cupo: asigna el canal por coincidencia temática.
        """
        accounts = [
            a for a in self.list_provider_accounts(provider_id)
            if a["status"] == ConnectionStatus.CONNECTED
        ]
        if not accounts:
            accounts = self.list_provider_accounts(provider_id)
        if not accounts:
            return None

        norm_ctx = _normalize_text(context_text)
        ctx_tokens = set(re.findall(r"[a-z0-9_]+", norm_ctx))

        best_acc = None
        best_score = -1
        best_matches: List[str] = []

        for acc in accounts:
            tags = acc.get("niche_list") or []
            matched: List[str] = []
            score = 0
            for tag in tags:
                norm_tag = _normalize_text(tag)
                if not norm_tag:
                    continue
                if " " in norm_tag:
                    if norm_tag in norm_ctx:
                        matched.append(tag)
                        score += 2
                elif norm_tag in ctx_tokens or norm_tag in norm_ctx:
                    matched.append(tag)
                    score += 1
            if score > best_score:
                best_score = score
                best_acc = acc
                best_matches = matched

        if best_score > 0 and best_acc is not None:
            return {
                "connection": best_acc,
                "matched_keywords": best_matches,
                "score": best_score,
                "reason": f"Coincidencia de nicho ({', '.join(best_matches)}) -> {best_acc['account_name']}",
            }

        fallback = next((a for a in accounts if a.get("is_default")), accounts[0])
        return {
            "connection": fallback,
            "matched_keywords": [],
            "score": 0,
            "reason": f"Sin palabras clave coincidentes; usando canal principal: {fallback['account_name']}",
        }

    def save_connection(
        self,
        provider_id: str,
        account_name: str,
        credentials: Dict[str, Any],
        test_now: bool = True,
        allow_mock: bool = False,
        connection_id: Optional[str] = None,
        create_new: bool = False,
        niche_tags: Optional[str] = None,
        daily_upload_limit: Optional[int] = None,
        is_default: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Cifra credenciales, ejecuta diagnóstico real y persiste la tarjeta del perfil."""
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Proveedor desconocido: {provider_id}")

        clean_creds = dict(credentials)
        if clean_creds.get("mock") and not (allow_mock and is_mock_allowed()):
            clean_creds.pop("mock", None)

        conn: Optional[AccountConnection] = None
        if connection_id and connection_id != "__new__":
            conn = self.get_connection_by_id(connection_id)
        elif not create_new and connection_id != "__new__":
            conn = self.get_connection(provider_id)

        existing_creds = {}
        if conn and conn.encrypted_credentials:
            existing_creds = decrypt_credentials(conn.encrypted_credentials)

        merged_creds = dict(existing_creds)
        for k, v in clean_creds.items():
            if v is not None and v != "":
                merged_creds[k] = v

        if not conn:
            has_any = self.db.query(AccountConnection).filter(AccountConnection.provider_id == provider_id.lower()).count() > 0
            conn = AccountConnection(
                provider_id=provider_id.lower(),
                account_name=account_name,
                niche_tags=(niche_tags or "").strip() or None,
                daily_upload_limit=max(1, int(daily_upload_limit or 6)),
                is_default=(not has_any) if is_default is None else bool(is_default),
            )
            self.db.add(conn)
        else:
            if niche_tags is not None:
                conn.niche_tags = niche_tags.strip() or None
            if daily_upload_limit is not None:
                conn.daily_upload_limit = max(1, int(daily_upload_limit))
            if is_default is not None:
                conn.is_default = bool(is_default)

        if conn.is_default:
            self.db.query(AccountConnection).filter(
                AccountConnection.provider_id == provider_id.lower(),
                AccountConnection.id != conn.id,
            ).update({"is_default": False})

        conn.account_name = account_name
        conn.encrypted_credentials = encrypt_credentials(merged_creds)
        conn.updated_at = utc_now()

        diag_ok = False
        diag_msg = "Credenciales cifradas y guardadas sin comprobación."
        diag_data: Dict[str, Any] = {}

        if test_now:
            diag_ok, diag_msg, diag_data = provider.test_connection(merged_creds)
            conn.last_checked_at = utc_now()
            self._apply_diagnostic_result(conn, merged_creds, diag_ok, diag_msg, diag_data)
        else:
            conn.status = ConnectionStatus.NEEDS_APPROVAL

        self.db.commit()
        self.db.refresh(conn)

        safe_diag = {k: v for k, v in diag_data.items() if not k.startswith("_")}
        return {
            "ok": diag_ok,
            "connection_id": conn.id,
            "status": conn.status,
            "message": redact_secrets(diag_msg),
            "diagnostics": safe_diag,
        }

    def update_account_profile(
        self,
        connection_id: str,
        account_name: Optional[str] = None,
        niche_tags: Optional[str] = None,
        daily_upload_limit: Optional[int] = None,
        is_default: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Actualiza metadatos de perfil (alias, nichos y cuota permitida) sin tocar tokens OAuth."""
        conn = self.get_connection_by_id(connection_id)
        if not conn:
            raise ValueError("Perfil de conexión no encontrado.")
        if account_name and account_name.strip():
            conn.account_name = account_name.strip()
        if niche_tags is not None:
            conn.niche_tags = niche_tags.strip() or None
        if daily_upload_limit is not None:
            conn.daily_upload_limit = max(1, int(daily_upload_limit))
        if is_default is True:
            self.db.query(AccountConnection).filter(
                AccountConnection.provider_id == conn.provider_id,
                AccountConnection.id != conn.id,
            ).update({"is_default": False})
            conn.is_default = True
        elif is_default is False:
            conn.is_default = False
        conn.updated_at = utc_now()
        self.db.commit()
        self.db.refresh(conn)
        return self._serialize_account(conn)

    def _apply_diagnostic_result(
        self,
        conn: AccountConnection,
        current_creds: Dict[str, Any],
        diag_ok: bool,
        diag_msg: str,
        diag_data: Dict[str, Any],
    ) -> None:
        if "_refreshed_credentials" in diag_data:
            refreshed = diag_data["_refreshed_credentials"]
            conn.encrypted_credentials = encrypt_credentials(refreshed)
            if refreshed.get("expires_in"):
                conn.expires_at = utc_now() + timedelta(seconds=int(refreshed["expires_in"]))

        if diag_ok:
            conn.status = ConnectionStatus.CONNECTED
            conn.last_error = None
            if diag_data.get("channel_id"):
                conn.account_id = str(diag_data["channel_id"])
            if diag_data.get("channel_title"):
                conn.account_name = str(diag_data["channel_title"])
        else:
            if diag_data.get("needs_oauth"):
                conn.status = ConnectionStatus.NEEDS_APPROVAL
            elif diag_data.get("status") == 401 or "expirado" in diag_msg.lower() or "401" in diag_msg:
                conn.status = ConnectionStatus.EXPIRED
            elif diag_data.get("status") == 403 or "permiso" in diag_msg.lower():
                conn.status = ConnectionStatus.INSUFFICIENT_PERMS
            else:
                conn.status = ConnectionStatus.ERROR
            conn.last_error = redact_secrets(diag_msg)

    def create_oauth_state(
        self,
        provider_id: str,
        redirect_uri: str,
        ttl_minutes: int = 10,
        connection_id: Optional[str] = None,
    ) -> str:
        """Genera y persiste un estado OAuth de un solo uso ligado opcionalmente a un perfil concreto."""
        state_val = secrets.token_urlsafe(32)
        entry = OAuthState(
            state=state_val,
            provider_id=provider_id.lower(),
            connection_id=connection_id,
            redirect_uri=redirect_uri,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
            used=False,
        )
        self.db.add(entry)
        self.db.commit()
        return state_val

    def consume_oauth_state(self, state_val: str, provider_id: str) -> Optional[OAuthState]:
        if not state_val:
            return None
        entry = (
            self.db.query(OAuthState)
            .filter(
                OAuthState.state == state_val,
                OAuthState.provider_id == provider_id.lower(),
            )
            .first()
        )
        if not entry or entry.used:
            return None

        now = utc_now()
        exp = entry.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < now:
            return None

        entry.used = True
        self.db.commit()
        return entry

    def update_oauth_tokens(
        self,
        provider_id: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_in: Optional[int] = None,
        scopes: Optional[List[str]] = None,
        verify_now: bool = True,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        extra_fields: Optional[Dict[str, Any]] = None,
        connection_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Actualiza los tokens OAuth cifrados del perfil indicado."""
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Proveedor desconocido: {provider_id}")

        conn = self.get_connection_by_id(connection_id) if connection_id else self.get_connection(provider_id)
        if not conn:
            has_any = self.db.query(AccountConnection).filter(AccountConnection.provider_id == provider_id.lower()).count() > 0
            conn = AccountConnection(
                provider_id=provider_id.lower(),
                account_name=f"{provider.info.name} Owner",
                daily_upload_limit=6,
                is_default=not has_any,
            )
            self.db.add(conn)

        creds = decrypt_credentials(conn.encrypted_credentials) if conn.encrypted_credentials else {}
        if client_id:
            creds["client_id"] = client_id.strip()
        if client_secret:
            creds["client_secret"] = client_secret.strip()
        if extra_fields:
            for k, v in extra_fields.items():
                if v is not None and v != "":
                    creds[k] = v
        creds["access_token"] = access_token
        if refresh_token:
            creds["refresh_token"] = refresh_token
        if expires_in:
            expires_at = utc_now() + timedelta(seconds=int(expires_in))
            creds["expires_at"] = expires_at.isoformat()
            conn.expires_at = expires_at
        if scopes:
            creds["scopes"] = scopes
            conn.scopes_json = json.dumps(scopes)

        conn.encrypted_credentials = encrypt_credentials(creds)
        conn.updated_at = utc_now()

        diag_ok = True
        diag_msg = "Tokens OAuth actualizados."
        diag_data: Dict[str, Any] = {}

        if verify_now:
            diag_ok, diag_msg, diag_data = provider.test_connection(creds)
            conn.last_checked_at = utc_now()
            self._apply_diagnostic_result(conn, creds, diag_ok, diag_msg, diag_data)

        self.db.commit()
        self.db.refresh(conn)

        safe_diag = {k: v for k, v in diag_data.items() if not k.startswith("_")}
        return {
            "ok": diag_ok,
            "connection_id": conn.id,
            "status": conn.status,
            "message": redact_secrets(diag_msg),
            "account_name": conn.account_name,
            "account_id": conn.account_id,
            "diagnostics": safe_diag,
        }

    def refresh_connection_tokens(self, provider_id: str, connection_id: Optional[str] = None) -> Dict[str, Any]:
        provider = get_provider(provider_id)
        if not provider:
            return {"ok": False, "status": ConnectionStatus.ERROR, "message": f"Proveedor '{provider_id}' no encontrado."}

        conn = self.get_connection_by_id(connection_id) if connection_id else self.get_connection(provider_id)
        if not conn or not conn.encrypted_credentials:
            return {"ok": False, "status": ConnectionStatus.NEEDS_CONFIG, "message": "No hay credenciales configuradas."}

        creds = decrypt_credentials(conn.encrypted_credentials)
        try:
            updated = provider.refresh_token(creds)
            return self.update_oauth_tokens(
                provider_id=provider_id,
                access_token=updated.get("access_token", ""),
                refresh_token=updated.get("refresh_token"),
                expires_in=updated.get("expires_in"),
                scopes=updated.get("scopes"),
                verify_now=True,
                connection_id=conn.id,
            )
        except Exception as e:
            msg = redact_secrets(str(e))
            conn.status = ConnectionStatus.EXPIRED
            conn.last_error = msg
            conn.last_checked_at = utc_now()
            self.db.commit()
            return {"ok": False, "status": conn.status, "message": msg}

    def test_connection_by_id(self, provider_id: str, connection_id: Optional[str] = None) -> Dict[str, Any]:
        """Ejecuta diagnóstico real del proveedor o de un perfil específico."""
        provider = get_provider(provider_id)
        if not provider:
            return {"ok": False, "status": ConnectionStatus.ERROR, "message": f"Proveedor '{provider_id}' no encontrado."}

        conn = self.get_connection_by_id(connection_id) if connection_id else self.get_connection(provider_id)
        if not conn or not conn.encrypted_credentials:
            return {"ok": False, "status": ConnectionStatus.NEEDS_CONFIG, "message": "No hay credenciales configuradas para este perfil."}

        creds = decrypt_credentials(conn.encrypted_credentials)
        ok, msg, details = provider.test_connection(creds)

        conn.last_checked_at = utc_now()
        self._apply_diagnostic_result(conn, creds, ok, msg, details)
        self.db.commit()

        safe_details = {k: v for k, v in details.items() if not k.startswith("_")}
        return {
            "ok": ok,
            "connection_id": conn.id,
            "status": conn.status,
            "message": redact_secrets(msg),
            "diagnostics": safe_details,
        }

    def disconnect_by_id(self, connection_id: str) -> bool:
        conn = self.get_connection_by_id(connection_id)
        if not conn:
            return False
        provider = get_provider(conn.provider_id)
        if provider and conn.encrypted_credentials:
            try:
                creds = decrypt_credentials(conn.encrypted_credentials)
                provider.disconnect(creds)
            except Exception as e:
                logger.warning("Error al revocar tokens en desconexión: %s", redact_secrets(str(e)))
        self.db.delete(conn)
        self.db.commit()
        return True

    def disconnect(self, provider_id: str) -> bool:
        """Revoca y elimina la(s) conexión(es) de un proveedor."""
        rows = (
            self.db.query(AccountConnection)
            .filter(AccountConnection.provider_id == provider_id.lower())
            .all()
        )
        if not rows:
            return False
        for conn in rows:
            self.disconnect_by_id(conn.id)
        return True
