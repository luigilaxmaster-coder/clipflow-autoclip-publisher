"""Servicio de gestión, OAuth 2.0 y diagnóstico de conexiones para Connected Apps Hub."""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.crypto import decrypt_credentials, encrypt_credentials
from clipflow.connections.providers import ALL_PROVIDERS, get_provider
from clipflow.models import AccountConnection, ConnectionStatus, OAuthState, utc_now
from clipflow.security import is_mock_allowed, redact_secrets

logger = logging.getLogger(__name__)


class ConnectionService:
    def __init__(self, db: Session):
        self.db = db

    def list_connections(self) -> List[Dict[str, Any]]:
        """
        Retorna la lista completa de proveedores integrados, indicando su estado real,
        límites documentados y detalles de la cuenta conectada.
        """
        db_connections = {c.provider_id: c for c in self.db.query(AccountConnection).all()}
        result = []

        for p in ALL_PROVIDERS:
            info = p.info
            db_conn = db_connections.get(info.id)

            if db_conn:
                status = db_conn.status
                account_name = db_conn.account_name
                account_id = db_conn.account_id
                last_checked = db_conn.last_checked_at.isoformat() if db_conn.last_checked_at else None
                last_error = db_conn.last_error
                expires_at = db_conn.expires_at.isoformat() if db_conn.expires_at else None
                conn_id = db_conn.id
                creds = decrypt_credentials(db_conn.encrypted_credentials) if db_conn.encrypted_credentials else {}
                has_oauth_client = bool(creds.get("client_id") and creds.get("client_secret"))
                has_refresh_token = bool(creds.get("refresh_token"))
                scopes = json.loads(db_conn.scopes_json) if db_conn.scopes_json else []
            else:
                status = ConnectionStatus.NEEDS_CONFIG
                account_name = None
                account_id = None
                last_checked = None
                last_error = None
                expires_at = None
                conn_id = None
                has_oauth_client = False
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
            })

        return result

    def get_connection(self, provider_id: str) -> Optional[AccountConnection]:
        return (
            self.db.query(AccountConnection)
            .filter(AccountConnection.provider_id == provider_id.lower())
            .first()
        )

    def get_decrypted_credentials(self, provider_id: str) -> Dict[str, Any]:
        conn = self.get_connection(provider_id)
        if not conn or not conn.encrypted_credentials:
            return {}
        return decrypt_credentials(conn.encrypted_credentials)

    def save_connection(
        self,
        provider_id: str,
        account_name: str,
        credentials: Dict[str, Any],
        test_now: bool = True,
        allow_mock: bool = False,
    ) -> Dict[str, Any]:
        """Cifra credenciales, ejecuta prueba de diagnóstico real y persiste el estado."""
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Proveedor desconocido: {provider_id}")

        clean_creds = dict(credentials)
        if clean_creds.get("mock") and not (allow_mock and is_mock_allowed()):
            # Prohibido inyectar mock via API o en producción
            clean_creds.pop("mock", None)

        conn = self.get_connection(provider_id)
        existing_creds = {}
        if conn and conn.encrypted_credentials:
            existing_creds = decrypt_credentials(conn.encrypted_credentials)

        # Preservar tokens existentes si solo se actualizaron client_id/client_secret
        merged_creds = dict(existing_creds)
        for k, v in clean_creds.items():
            if v is not None and v != "":
                merged_creds[k] = v

        if not conn:
            conn = AccountConnection(provider_id=provider_id.lower(), account_name=account_name)
            self.db.add(conn)

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

        # No retornar secretos internos en respuesta
        safe_diag = {k: v for k, v in diag_data.items() if not k.startswith("_")}
        return {
            "ok": diag_ok,
            "status": conn.status,
            "message": redact_secrets(diag_msg),
            "diagnostics": safe_diag,
        }

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

    def create_oauth_state(self, provider_id: str, redirect_uri: str, ttl_minutes: int = 10) -> str:
        """Genera y persiste un estado OAuth de un solo uso con expiración corta."""
        state_val = secrets.token_urlsafe(32)
        entry = OAuthState(
            state=state_val,
            provider_id=provider_id.lower(),
            redirect_uri=redirect_uri,
            expires_at=utc_now() + timedelta(minutes=ttl_minutes),
            used=False,
        )
        self.db.add(entry)
        self.db.commit()
        return state_val

    def consume_oauth_state(self, state_val: str, provider_id: str) -> Optional[OAuthState]:
        """
        Valida y consume atómicamente un estado OAuth.
        Retorna el registro si es válido, no usado y no expirado; None en caso contrario.
        """
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
    ) -> Dict[str, Any]:
        """Actualiza los tokens OAuth cifrados preservando client_id/client_secret y refresh_token previo."""
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Proveedor desconocido: {provider_id}")

        conn = self.get_connection(provider_id)
        if not conn:
            conn = AccountConnection(provider_id=provider_id.lower(), account_name=f"{provider.info.name} Owner")
            self.db.add(conn)

        creds = decrypt_credentials(conn.encrypted_credentials) if conn.encrypted_credentials else {}
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
            "status": conn.status,
            "message": redact_secrets(diag_msg),
            "account_name": conn.account_name,
            "account_id": conn.account_id,
            "diagnostics": safe_diag,
        }

    def refresh_connection_tokens(self, provider_id: str) -> Dict[str, Any]:
        """Renueva el token OAuth usando el refresh_token cifrado y re-verifica la conexión."""
        provider = get_provider(provider_id)
        if not provider:
            return {"ok": False, "status": ConnectionStatus.ERROR, "message": f"Proveedor '{provider_id}' no encontrado."}

        conn = self.get_connection(provider_id)
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
            )
        except Exception as e:
            msg = redact_secrets(str(e))
            conn.status = ConnectionStatus.EXPIRED
            conn.last_error = msg
            conn.last_checked_at = utc_now()
            self.db.commit()
            return {"ok": False, "status": conn.status, "message": msg}

    def test_connection_by_id(self, provider_id: str) -> Dict[str, Any]:
        """Ejecuta diagnóstico real utilizando las credenciales cifradas almacenadas."""
        provider = get_provider(provider_id)
        if not provider:
            return {"ok": False, "status": ConnectionStatus.ERROR, "message": f"Proveedor '{provider_id}' no encontrado."}

        conn = self.get_connection(provider_id)
        if not conn or not conn.encrypted_credentials:
            return {"ok": False, "status": ConnectionStatus.NEEDS_CONFIG, "message": "No hay credenciales configuradas para este proveedor."}

        creds = decrypt_credentials(conn.encrypted_credentials)
        ok, msg, details = provider.test_connection(creds)

        conn.last_checked_at = utc_now()
        self._apply_diagnostic_result(conn, creds, ok, msg, details)
        self.db.commit()

        safe_details = {k: v for k, v in details.items() if not k.startswith("_")}
        return {
            "ok": ok,
            "status": conn.status,
            "message": redact_secrets(msg),
            "diagnostics": safe_details,
        }

    def disconnect(self, provider_id: str) -> bool:
        """Revoca y elimina la conexión de un proveedor."""
        conn = self.get_connection(provider_id)
        if not conn:
            return False

        provider = get_provider(provider_id)
        if provider and conn.encrypted_credentials:
            try:
                creds = decrypt_credentials(conn.encrypted_credentials)
                provider.disconnect(creds)
            except Exception as e:
                logger.warning("Error al revocar tokens en desconexión: %s", redact_secrets(str(e)))

        self.db.delete(conn)
        self.db.commit()
        return True
