"""Pruebas de seguridad de propietario único, CSRF, endpoints de salud y respaldos."""
from pathlib import Path
from starlette.testclient import TestClient

from clipflow.db import init_db
from clipflow.security import COOKIE_NAME, CSRF_COOKIE_NAME, get_owner_token
from clipflow.web.app import app
from scripts.backup import create_backup
from scripts.restore import restore_backup


def test_single_owner_auth_and_health(monkeypatch):
    # 0. Sin OWNER_TOKEN ni archivo .owner.token no debe existir valor predeterminado fijo
    monkeypatch.delenv("OWNER_TOKEN", raising=False)
    monkeypatch.setattr("clipflow.security.OWNER_TOKEN_FILE", Path("nonexistent_dir/.owner.token"))
    assert get_owner_token() is None

    # 1. Configurar OWNER_TOKEN explícito para la prueba
    test_token = "unit-test-owner-token-998877665544332211"
    monkeypatch.setenv("OWNER_TOKEN", test_token)

    init_db()
    client = TestClient(app)

    # Health check endpoint público
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    data = res_health.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["database_connected"] is True

    # Status API bloqueada sin autenticación
    res_unauth = client.get("/api/status")
    assert res_unauth.status_code == 401

    # Status API permitida con Bearer
    res_status = client.get("/api/status", headers={"Authorization": f"Bearer {test_token}"})
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "ok"

    # Login flow establece cookie firmada (NO el token plano) + cookie CSRF
    login_res = client.post("/login", data={"token": test_token}, follow_redirects=False)
    assert login_res.status_code == 303
    cookie_hdr = login_res.headers.get("set-cookie", "")
    assert COOKIE_NAME in cookie_hdr
    assert CSRF_COOKIE_NAME in cookie_hdr
    assert test_token not in cookie_hdr


def test_backup_and_restore_cycle(tmp_path: Path):
    backup_zip = create_backup()
    assert backup_zip.is_file()
    assert backup_zip.stat().st_size > 0

    restored = restore_backup(str(backup_zip))
    assert restored is True
