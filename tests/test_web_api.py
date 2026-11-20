"""Pruebas del panel de administración web y endpoints FastAPI con autenticación obligatoria."""
from pathlib import Path
from starlette.testclient import TestClient

from clipflow.db import init_db
from clipflow.web.app import app


def test_web_admin_pages(monkeypatch, db_session, vertical_video_path: Path):
    monkeypatch.setenv("OWNER_TOKEN", "test-owner-secret-token-32chars-minimum-ok")
    init_db()
    client = TestClient(app)

    # 0. Sin autenticación: HTML redirige a /login y API devuelve 401
    unauth_html = client.get("/", follow_redirects=False)
    assert unauth_html.status_code == 303
    assert "/login" in unauth_html.headers["location"]

    unauth_api = client.get("/api/status")
    assert unauth_api.status_code == 401

    headers = {"Authorization": "Bearer test-owner-secret-token-32chars-minimum-ok"}

    # 1. Dashboard autenticado
    res = client.get("/", headers=headers)
    assert res.status_code == 200
    assert "ClipFlow" in res.text
    assert "Proyectos de Producción" in res.text

    # 2. Queue view
    res = client.get("/queue", headers=headers)
    assert res.status_code == 200
    assert "Trabajos de Publicación" in res.text

    # 3. Audit view
    res = client.get("/audit", headers=headers)
    assert res.status_code == 200
    assert "Eventos de Auditoría" in res.text

    # 4. Status API
    res = client.get("/api/status", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
