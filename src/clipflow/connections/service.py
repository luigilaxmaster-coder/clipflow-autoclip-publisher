"""Servicio de gestión y diagnóstico de conexiones para Connected Apps Hub."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from clipflow.connections.crypto import decrypt_credentials, encrypt_credentials
from clipflow.connections.providers import ALL_PROVIDERS, PROVIDERS_MAP, get_provider
from clipflow.models import AccountConnection, ConnectionStatus, utc_now

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
                last_checked = db_conn.last_checked_at.isoformat() if db_conn.last_checked_at else None
                last_error = db_conn.last_error
                conn_id = db_conn.id
            else:
                status = ConnectionStatus.NEEDS_CONFIG
                account_name = None
                last_checked = None
                last_error = None
                conn_id = None

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
                "connection_id": conn_id,
                "last_checked_at": last_checked,
                "last_error": last_error,
            })

        return result

    def get_connection(self, provider_id: str) -> Optional[AccountConnection]:
        return self.db.query(AccountConnection).filter(AccountConnection.provider_id == provider_id.lower()).first()

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
    ) -> Dict[str, Any]:
        """Cifra credenciales, ejecuta prueba de diagnóstico y persiste el estado."""
        provider = get_provider(provider_id)
        if not provider:
            raise ValueError(f"Proveedor desconocido: {provider_id}")

        conn = self.get_connection(provider_id)
        if not conn:
            conn = AccountConnection(provider_id=provider_id.lower(), account_name=account_name)
            self.db.add(conn)

        conn.account_name = account_name
        conn.encrypted_credentials = encrypt_credentials(credentials)
        conn.updated_at = utc_now()

        diag_ok = False
        diag_msg = "Credenciales guardadas sin comprobación."
        diag_data = {}

        if test_now:
            diag_ok, diag_msg, diag_data = provider.test_connection(credentials)
            conn.last_checked_at = utc_now()
            if diag_ok:
                conn.status = ConnectionStatus.CONNECTED
                conn.last_error = None
            else:
                conn.status = ConnectionStatus.ERROR
                conn.last_error = diag_msg

        self.db.commit()
        self.db.refresh(conn)

        return {
            "ok": diag_ok,
            "status": conn.status,
            "message": diag_msg,
            "diagnostics": diag_data,
        }

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
        if ok:
            conn.status = ConnectionStatus.CONNECTED
            conn.last_error = None
        else:
            if "expirado" in msg.lower() or "401" in msg:
                conn.status = ConnectionStatus.EXPIRED
            else:
                conn.status = ConnectionStatus.ERROR
            conn.last_error = msg

        self.db.commit()
        return {
            "ok": ok,
            "status": conn.status,
            "message": msg,
            "diagnostics": details,
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
                logger.warning("Error al revocar tokens en desconexión: %s", e)

        self.db.delete(conn)
        self.db.commit()
        return True
