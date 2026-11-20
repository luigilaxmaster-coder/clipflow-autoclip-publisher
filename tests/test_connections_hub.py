"""Pruebas del hub de conexiones, cifrado Fernet y diagnósticos de proveedores."""
import pytest
from sqlalchemy.orm import Session

from clipflow.connections.crypto import decrypt_credentials, encrypt_credentials
from clipflow.connections.providers import ALL_PROVIDERS, get_provider
from clipflow.connections.service import ConnectionService
from clipflow.models import AccountConnection, ConnectionStatus


def test_fernet_credential_encryption():
    raw_creds = {
        "client_id": "google-client-id-12345.apps.googleusercontent.com",
        "client_secret": "GOCSPX-super_secret_oauth_key",
        "refresh_token": "1//04refreshtokenstring",
    }
    encrypted = encrypt_credentials(raw_creds)
    assert isinstance(encrypted, str)
    assert "super_secret_oauth_key" not in encrypted

    decrypted = decrypt_credentials(encrypted)
    assert decrypted["client_id"] == raw_creds["client_id"]
    assert decrypted["client_secret"] == raw_creds["client_secret"]
    assert decrypted["refresh_token"] == raw_creds["refresh_token"]


def test_all_18_providers_registered():
    assert len(ALL_PROVIDERS) == 18
    provider_ids = [p.info.id for p in ALL_PROVIDERS]
    expected = [
        "youtube", "tiktok", "instagram", "facebook", "threads", "x",
        "linkedin", "pinterest", "bluesky", "reddit", "telegram", "discord",
        "wordpress", "google_business", "google_drive", "onedrive", "dropbox", "local_directory"
    ]
    for exp in expected:
        assert exp in provider_ids, f"Proveedor {exp} debe estar registrado"


def test_connection_service_lifecycle(db_session: Session):
    service = ConnectionService(db_session)

    # 1. Listar inicialmente
    all_conns = service.list_connections()
    assert len(all_conns) == 18

    # 2. Guardar credenciales de YouTube con diagnóstico en mock
    res = service.save_connection(
        provider_id="youtube",
        account_name="@CanalPrincipal",
        credentials={"access_token": "mock-token", "mock": True},
        test_now=True,
    )
    assert res["ok"] is True
    assert res["status"] == ConnectionStatus.CONNECTED

    # 3. Comprobar que en BD está cifrado
    db_conn = db_session.query(AccountConnection).filter(AccountConnection.provider_id == "youtube").first()
    assert db_conn is not None
    assert db_conn.status == ConnectionStatus.CONNECTED
    assert "mock-token" not in db_conn.encrypted_credentials

    # 4. Probar diagnóstico por ID
    test_res = service.test_connection_by_id("youtube")
    assert test_res["ok"] is True

    # 5. Desconectar
    assert service.disconnect("youtube") is True
    assert service.get_connection("youtube") is None
