"""Pruebas funcionales y de regresión visual/arquitectónica para Dashboard V2 y OAuth YouTube."""
from __future__ import annotations

from unittest.mock import MagicMock, patch
from starlette.testclient import TestClient

from clipflow.connections.service import ConnectionService
from clipflow.db import get_session, init_db
from clipflow.models import ConnectionStatus, JobStatus, PostDraft, Project, PublishJob, Render, Source
from clipflow.web.app import app


def test_v2_all_10_screens_load_local_design_system_without_cdn(monkeypatch):
    token = "v2-ui-owner-token-9876543210abcdef"
    monkeypatch.setenv("OWNER_TOKEN", token)
    init_db()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # Activos estáticos locales del Design System
    for asset in ("/static/css/design-system.css", "/static/js/app.js", "/static/js/charts.js"):
        res = client.get(asset)
        assert res.status_code == 200, f"Fallo al servir activo estático local {asset}"
        assert len(res.text) > 500

    # Todas las 10 secciones + audit + login
    screens = [
        "/", "/overview", "/connections", "/publisher", "/calendar",
        "/media-library", "/analytics", "/inbox", "/automations",
        "/queue", "/settings", "/audit",
    ]
    for path in screens:
        res = client.get(path, headers=headers)
        assert res.status_code == 200, f"Error en pantalla {path}: HTTP {res.status_code}"
        assert "/static/css/design-system.css" in res.text
        assert "cdn.tailwindcss.com" not in res.text, f"{path} todavía usa Tailwind CDN de desarrollo"
        assert "cdnjs.cloudflare.com" not in res.text, f"{path} todavía depende de CDN externo"
        assert 'name="csrf-token"' in res.text


def test_youtube_connect_button_ready_from_env_without_marking_connected(monkeypatch, tmp_path):
    token = "v2-oauth-env-token-9876543210abcdef"
    monkeypatch.setenv("OWNER_TOKEN", token)
    monkeypatch.setenv("YOUTUBE_CLIENT_ID", "env-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("YOUTUBE_CLIENT_SECRET", "env-client-secret-value")
    db_file = tmp_path / "yt_env_v2.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    # Limpiar posible registro previo de youtube
    sess = get_session()
    try:
        ConnectionService(sess).disconnect("youtube")
    finally:
        sess.close()

    client = TestClient(app)
    res = client.get("/connections", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert "Conectar YouTube" in res.text
    assert "/api/connections/youtube/callback" in res.text
    assert "/api/connections/youtube/oauth/start" in res.text
    # No debe figurar como Conectado sin completar OAuth real
    assert "Pendiente OAuth" in res.text


def test_calendar_reschedule_and_published_protection(monkeypatch, tmp_path):
    token = "v2-calendar-owner-token-9876543210"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "calendar_v2.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        src = Source(source_kind="file", canonical_source="v.mp4", local_path="v.mp4")
        sess.add(src)
        sess.flush()
        proj = Project(source_id=src.id, workflow_status="COMPLETED")
        sess.add(proj)
        sess.flush()
        rnd = Render(project_id=proj.id, target_platform="youtube", video_path="v.mp4")
        sess.add(rnd)
        sess.flush()
        d1 = PostDraft(render_id=rnd.id, platform="youtube", title="Movable Short")
        d2 = PostDraft(render_id=rnd.id, platform="youtube", title="Already Published Short")
        sess.add_all([d1, d2])
        sess.flush()
        j_sched = PublishJob(post_draft_id=d1.id, idempotency_key="cal-1", status=JobStatus.SCHEDULED)
        j_pub = PublishJob(post_draft_id=d2.id, idempotency_key="cal-2", status=JobStatus.PUBLISHED, remote_post_id="VID_OK")
        sess.add_all([j_sched, j_pub])
        sess.commit()
        j_sched_id, j_pub_id = j_sched.id, j_pub.id
    finally:
        sess.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}

    # Reprogramar trabajo SCHEDULED -> convierte 10:00 Santo Domingo (-04:00) a 14:00 UTC
    r_ok = client.post(
        f"/api/jobs/{j_sched_id}/reschedule",
        headers=headers,
        json={"scheduled_time": "2026-12-20T10:00:00"},
    )
    assert r_ok.status_code == 200
    assert "2026-12-20T14:00:00" in r_ok.json()["scheduled_at_utc"]

    # Reprogramar o cancelar un trabajo PUBLISHED debe bloquearse con 409
    r_pub = client.post(
        f"/api/jobs/{j_pub_id}/reschedule",
        headers=headers,
        json={"scheduled_time": "2026-12-21T10:00:00"},
    )
    assert r_pub.status_code == 409

    r_cancel = client.post(f"/api/jobs/{j_pub_id}/cancel", headers=headers)
    assert r_cancel.status_code == 409


def test_youtube_oauth_refresh_endpoint_updates_encrypted_tokens(monkeypatch, tmp_path):
    token = "v2-refresh-owner-token-9876543210"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "refresh_v2.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        svc = ConnectionService(sess)
        svc.save_connection(
            provider_id="youtube",
            account_name="Owner Channel",
            credentials={
                "client_id": "cid-ref",
                "client_secret": "csec-ref",
                "access_token": "old-expired-token",
                "refresh_token": "valid-refresh-token",
            },
            test_now=False,
        )
    finally:
        sess.close()

    fake_refresh = MagicMock(status_code=200)
    fake_refresh.json.return_value = {"access_token": "brand-new-access-token", "expires_in": 3600}
    fake_chan = MagicMock(status_code=200)
    fake_chan.json.return_value = {
        "items": [{"id": "UC_REFRESHED_1", "snippet": {"title": "Canal Renovado"}, "statistics": {"subscriberCount": "100"}}]
    }

    client = TestClient(app)
    with patch("clipflow.connections.providers.requests.post", return_value=fake_refresh), \
         patch("clipflow.connections.providers.requests.get", return_value=fake_chan):
        res = client.post(
            "/api/connections/youtube/oauth/refresh",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["ok"] is True
        assert data["status"] == ConnectionStatus.CONNECTED
        assert data["account_id"] == "UC_REFRESHED_1"

    sess2 = get_session()
    try:
        creds = ConnectionService(sess2).get_decrypted_credentials("youtube")
        assert creds["access_token"] == "brand-new-access-token"
        assert creds["refresh_token"] == "valid-refresh-token"
    finally:
        sess2.close()
