"""Pruebas del panel de administración web y endpoints FastAPI."""
from pathlib import Path
from starlette.testclient import TestClient

from clipflow.db import init_db
from clipflow.models import PostDraft, Project, Render, Source
from clipflow.web.app import app


def test_web_admin_pages(db_session, vertical_video_path: Path):
    init_db()
    client = TestClient(app)

    # 1. Dashboard
    res = client.get("/")
    assert res.status_code == 200
    assert "ClipFlow" in res.text
    assert "Proyectos de Producción" in res.text

    # 2. Queue view
    res = client.get("/queue")
    assert res.status_code == 200
    assert "Trabajos de Publicación" in res.text

    # 3. Audit view
    res = client.get("/audit")
    assert res.status_code == 200
    assert "Eventos de Auditoría" in res.text

    # 4. Status API
    res = client.get("/api/status")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"
