"""Pruebas de seguridad de propietario único, endpoints de salud y respaldos."""
import os
from pathlib import Path
from starlette.testclient import TestClient

from clipflow.db import init_db
from clipflow.security import get_owner_token, verify_owner_access
from clipflow.web.app import app
from scripts.backup import create_backup
from scripts.restore import restore_backup


def test_single_owner_auth_and_health():
    init_db()
    client = TestClient(app)

    # 1. Health check endpoint
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    data = res_health.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["database_connected"] is True

    # 2. Status API
    res_status = client.get("/api/status")
    assert res_status.status_code == 200
    assert res_status.json()["status"] == "ok"

    # 3. Owner token verification
    token = get_owner_token()
    assert len(token) >= 8

    # 4. Login flow
    login_res = client.post("/login", data={"token": token}, follow_redirects=False)
    assert login_res.status_code == 303
    assert "clipflow_owner_session" in login_res.headers.get("set-cookie", "")


def test_backup_and_restore_cycle(tmp_path: Path):
    # 1. Ejecutar respaldo
    backup_zip = create_backup()
    assert backup_zip.is_file()
    assert backup_zip.stat().st_size > 0

    # 2. Ejecutar restauración verificada
    restored = restore_backup(str(backup_zip))
    assert restored is True
