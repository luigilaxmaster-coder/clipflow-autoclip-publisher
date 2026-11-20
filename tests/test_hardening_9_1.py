"""Suite de regresión de Fase 9.1 — Hardening de seguridad, OAuth, YouTube resumible, Analytics y Worker."""
from __future__ import annotations

import io
import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from clipflow.connections.cli_store import main as cli_store_main
from clipflow.connections.service import ConnectionService
from clipflow.db import init_db
from clipflow.models import (
    AccountConnection,
    Base,
    ConnectionStatus,
    JobStatus,
    PostDraft,
    Project,
    PublishJob,
    Render,
    Source,
    utc_now,
)
from clipflow.publishing.native_youtube import NativeYouTubePublisher
from clipflow.publishing.postiz import PostizPublisher
from clipflow.publishing.unified import UnifiedPublisher
from clipflow.queue import PublishQueueWorker
from clipflow.security import (
    COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    redact_secrets,
)
from clipflow.supervisor import build_managed_processes
from clipflow.web.app import app


# -----------------------------------------------------------------------------
# 1. Autenticación obligatoria en todas las rutas + CSRF en cookies
# -----------------------------------------------------------------------------
def test_all_admin_routes_require_auth_no_localhost_bypass(monkeypatch):
    token = "hardening-owner-secret-token-2026-xyz"
    monkeypatch.setenv("OWNER_TOKEN", token)
    # Incluso si alguien intenta reactivar una variable legacy, no hay bypass
    monkeypatch.setenv("ALLOW_LOCALHOST_DEV", "true")
    init_db()
    client = TestClient(app)

    html_routes = [
        "/", "/overview", "/connections", "/publisher", "/calendar",
        "/media-library", "/analytics", "/inbox", "/automations", "/queue", "/settings", "/audit",
    ]
    for route in html_routes:
        res = client.get(route, follow_redirects=False)
        assert res.status_code == 303, f"Ruta HTML {route} no redirigió a login sin sesión"
        assert "/login" in res.headers["location"]

    api_get_routes = ["/api/status", "/api/analytics/summary", "/api/connections/youtube/oauth/start"]
    for route in api_get_routes:
        res = client.get(route, follow_redirects=False)
        assert res.status_code == 401, f"Endpoint API {route} permitió acceso sin sesión"

    api_post_routes = [
        ("/api/worker/tick", {}),
        ("/api/connections/youtube/test", {}),
        ("/api/system/backup", {}),
    ]
    for route, body in api_post_routes:
        res = client.post(route, json=body, follow_redirects=False)
        assert res.status_code == 401, f"POST {route} permitió acceso sin sesión"


def test_cookie_mutations_enforce_csrf_protection(monkeypatch):
    token = "csrf-test-owner-secret-token-99999"
    monkeypatch.setenv("OWNER_TOKEN", token)
    init_db()
    client = TestClient(app)

    # Login por formulario para obtener cookie firmada + cookie CSRF
    login = client.post("/login", data={"token": token}, follow_redirects=False)
    assert login.status_code == 303
    assert COOKIE_NAME in client.cookies
    csrf_val = client.cookies.get(CSRF_COOKIE_NAME)
    assert csrf_val

    # POST con cookie pero SIN CSRF debe devolver 403
    blocked = client.post("/api/connections/youtube/test")
    assert blocked.status_code == 403

    # POST con cookie y CSRF inválido debe devolver 403
    blocked_bad = client.post(
        "/api/connections/youtube/test",
        headers={CSRF_HEADER_NAME: "token-invalido"},
    )
    assert blocked_bad.status_code == 403

    # POST con cookie y CSRF válido debe ser aceptado (200)
    allowed = client.post(
        "/api/connections/youtube/test",
        headers={CSRF_HEADER_NAME: csrf_val},
    )
    assert allowed.status_code == 200


# -----------------------------------------------------------------------------
# 2. Cero fuga de secretos y entrada segura por stdin (cli_store + redact)
# -----------------------------------------------------------------------------
def test_secret_redaction_and_cli_store_stdin_separation(monkeypatch, tmp_path: Path):
    leaked_str = "Error con Bearer ya29.a0AfH6SMB_secret_token y client_secret=GOCSPX-mySecret123 y 1//04refreshTok"
    cleaned = redact_secrets(leaked_str)
    assert "ya29.a0AfH6SMB_secret_token" not in cleaned
    assert "GOCSPX-mySecret123" not in cleaned
    assert "1//04refreshTok" not in cleaned
    assert "[REDACTED]" in cleaned

    # Verificar que cli_store rechaza access_token == refresh_token
    same_tok_payload = json.dumps({
        "client_id": "cid-123",
        "client_secret": "csec-456",
        "access_token": "identical-token",
        "refresh_token": "identical-token",
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(same_tok_payload))
    rc = cli_store_main(["--provider", "youtube", "--no-test"])
    assert rc == 1

    # Verificar que cli_store acepta client_id + client_secret por stdin sin imprimir secretos
    db_file = tmp_path / "cli_store_test.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)

    valid_payload = json.dumps({
        "client_id": "my-real-client-id.apps.googleusercontent.com",
        "client_secret": "GOCSPX-UltraSecretShouldNeverPrint",
    })
    monkeypatch.setattr("sys.stdin", io.StringIO(valid_payload))
    rc_ok = cli_store_main(["--provider", "youtube", "--no-test"])
    assert rc_ok == 0


# -----------------------------------------------------------------------------
# 3. Prohibición estricta del modo mock en producción
# -----------------------------------------------------------------------------
def test_mock_mode_strictly_blocked_in_production(monkeypatch, db_session: Session):
    monkeypatch.setenv("AUTOCLIP_ENV", "production")

    with pytest.raises(RuntimeError, match="prohibido"):
        NativeYouTubePublisher(mock_mode=True)

    with pytest.raises(RuntimeError, match="prohibido"):
        PostizPublisher(mock_mode=True)

    with pytest.raises(RuntimeError, match="prohibido"):
        UnifiedPublisher(mock_mode=True)

    # Ningún proveedor puede pasar a CONNECTED mediante credentials={"mock": True}
    conn_svc = ConnectionService(db_session)
    res = conn_svc.save_connection(
        provider_id="youtube",
        account_name="FakeAccount",
        credentials={"access_token": "fake", "mock": True},
        test_now=True,
        allow_mock=False,
    )
    assert res["ok"] is False
    conn = conn_svc.get_connection("youtube")
    assert conn is not None
    assert conn.status != ConnectionStatus.CONNECTED


# -----------------------------------------------------------------------------
# 4. Flujo OAuth 2.0 completo (estado de un solo uso + callback + cifrado)
# -----------------------------------------------------------------------------
def test_google_oauth_state_validation_and_callback_flow(monkeypatch, tmp_path: Path):
    token = "oauth-owner-token-1234567890abcdef"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "oauth_flow.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    # Guardar client_id y client_secret en la BD real usada por FastAPI
    from clipflow.db import get_session
    app_db = get_session()
    try:
        conn_svc = ConnectionService(app_db)
        conn_svc.save_connection(
            provider_id="youtube",
            account_name="Owner Channel",
            credentials={"client_id": "cid-oauth-test", "client_secret": "csec-oauth-test"},
            test_now=False,
        )
    finally:
        app_db.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    # 1. Iniciar OAuth genera auth_url con estado único persistido
    start_res = client.get("/api/connections/youtube/oauth/start", headers=headers)
    assert start_res.status_code == 200
    auth_url = start_res.json()["auth_url"]
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_url
    assert "state=" in auth_url

    from urllib.parse import parse_qs, urlparse
    state_val = parse_qs(urlparse(auth_url).query)["state"][0]

    # 2. Callback con estado inválido debe rechazarse (400)
    bad_cb = client.get(
        "/api/connections/youtube/callback?code=abc&state=invalid-state",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert bad_cb.status_code == 400

    # 3. Callback con estado válido canjea código, cifra tokens y verifica canal real (parcheando solo HTTP externo)
    fake_token_resp = MagicMock(status_code=200)
    fake_token_resp.json.return_value = {
        "access_token": "ya29.real_exchanged_token",
        "refresh_token": "1//real_refresh_token",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
    }
    fake_chan_resp = MagicMock(status_code=200)
    fake_chan_resp.json.return_value = {
        "items": [{
            "id": "UC_REAL_CHANNEL_999",
            "snippet": {"title": "Mi Canal Verificado"},
            "statistics": {"subscriberCount": "4200"},
        }]
    }

    with patch("clipflow.publishing.native_youtube.requests.post", return_value=fake_token_resp), \
         patch("clipflow.connections.providers.requests.get", return_value=fake_chan_resp):
        ok_cb = client.get(
            f"/api/connections/youtube/callback?code=google_code_xyz&state={state_val}",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )
        assert ok_cb.status_code == 303
        assert "oauth=success" in ok_cb.headers["location"]

    # 4. Reutilizar el mismo estado OAuth (replay) debe fallar con 400
    replay_cb = client.get(
        f"/api/connections/youtube/callback?code=google_code_xyz&state={state_val}",
        headers={"Authorization": f"Bearer {token}"},
        follow_redirects=False,
    )
    assert replay_cb.status_code == 400

    # 5. Verificar que UnifiedPublisher obtiene exactamente la conexión cifrada guardada
    verify_db = get_session()
    try:
        svc = ConnectionService(verify_db)
        conn = svc.get_connection("youtube")
        assert conn is not None
        assert conn.status == ConnectionStatus.CONNECTED
        assert conn.account_id == "UC_REAL_CHANNEL_999"
        assert "ya29.real_exchanged_token" not in conn.encrypted_credentials

        pub = UnifiedPublisher(connection_service=svc, mock_mode=False)
        assert pub.native_youtube.client_id == "cid-oauth-test"
        assert pub.native_youtube.access_token == "ya29.real_exchanged_token"
        assert pub.native_youtube.refresh_token == "1//real_refresh_token"
    finally:
        verify_db.close()


# -----------------------------------------------------------------------------
# 5. Subida resumible YouTube por bloques, recuperación sin duplicar y video_id real
# -----------------------------------------------------------------------------
def test_youtube_resumable_upload_and_no_duplicate_on_retry(tmp_path: Path):
    # Crear archivo de 600 KB para probar 2 bloques de 256 KB + resto
    video_file = tmp_path / "chunked_test.mp4"
    video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"A" * (600 * 1024))

    yt = NativeYouTubePublisher(
        client_id="cid",
        client_secret="csec",
        access_token="tok",
        refresh_token="ref",
        mock_mode=False,
        chunk_size=256 * 1024,
    )

    session_uris: list[str] = []
    sent_offsets: list[int] = []

    # Simular: POST inicia sesión, chunk 1 devuelve 308 (bytes=0-262143), chunk 2 falla con 503
    init_resp = MagicMock(status_code=200, headers={"Location": "https://upload.youtube.test/session/abc123"})
    chunk1_resp = MagicMock(status_code=308, headers={"Range": "bytes=0-262143"})
    chunk2_fail = MagicMock(status_code=503, headers={})

    with patch("clipflow.publishing.native_youtube.requests.request", return_value=init_resp) as mock_req, \
         patch("clipflow.publishing.native_youtube.requests.put", side_effect=[chunk1_resp, chunk2_fail]):
        res1 = yt.submit_post(
            video_path=str(video_file),
            title="Test Resumable",
            platform="youtube",
            profile="owner",
            visibility="private",
            on_session_created=lambda u: session_uris.append(u),
            on_progress=lambda b: sent_offsets.append(b),
        )
        assert res1.ok is False
        assert session_uris == ["https://upload.youtube.test/session/abc123"]
        assert sent_offsets == [262144]
        # Verificar que el metadata inicial envió privacyStatus=private explícitamente
        sent_meta = json.loads(mock_req.call_args.kwargs["data"])
        assert sent_meta["status"]["privacyStatus"] == "private"

    # En el reintento con existing_session_uri:
    # Sondeo PUT bytes */total responde 200 con el video_id real (la subida ya se había completado en el servidor)
    # -> NO debe abrir una nueva sesión POST ni re-subir el video!
    probe_done = MagicMock(status_code=200)
    probe_done.json.return_value = {"id": "REAL_YT_VID_777"}
    verify_resp = MagicMock(status_code=200)
    verify_resp.json.return_value = {
        "items": [{
            "id": "REAL_YT_VID_777",
            "snippet": {"channelId": "UC_OWNER_1"},
            "status": {"privacyStatus": "private", "uploadStatus": "uploaded"},
            "processingDetails": {"processingStatus": "succeeded"},
        }]
    }

    with patch("clipflow.publishing.native_youtube.requests.put", return_value=probe_done) as mock_put, \
         patch("clipflow.publishing.native_youtube.requests.request", return_value=verify_resp) as mock_verify:
        res2 = yt.submit_post(
            video_path=str(video_file),
            title="Test Resumable",
            platform="youtube",
            profile="owner",
            visibility="private",
            existing_session_uri="https://upload.youtube.test/session/abc123",
        )
        assert res2.ok is True
        assert res2.remote_post_id == "REAL_YT_VID_777"
        assert res2.remote_url == "https://www.youtube.com/shorts/REAL_YT_VID_777"
        assert res2.raw_payload["channelId"] == "UC_OWNER_1"
        assert res2.raw_payload["privacyStatus"] == "private"
        # Solo 1 PUT de sondeo (Content-Range: bytes */total) y 1 GET videos.list; NINGÚN POST nuevo
        assert mock_put.call_count == 1
        assert mock_verify.call_args.args[0] == "GET"


# -----------------------------------------------------------------------------
# 6. Analytics reales o SIN_DATOS (cero números artificiales)
# -----------------------------------------------------------------------------
def test_analytics_returns_sin_datos_when_no_real_api_data(monkeypatch, tmp_path: Path):
    token = "analytics-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "analytics_real.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    from clipflow.db import get_session
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
        draft = PostDraft(render_id=rnd.id, platform="youtube", title="Clip Sin Stats")
        sess.add(draft)
        sess.flush()
        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key="analytics-test-1",
            status=JobStatus.PUBLISHED,
            remote_post_id="VID_NO_CONN",
            remote_url="https://www.youtube.com/shorts/VID_NO_CONN",
        )
        sess.add(job)
        sess.commit()
    finally:
        sess.close()

    client = TestClient(app)
    res = client.get("/api/analytics/summary", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert data["metrics_status"] == "SIN_DATOS"
    assert data["total_views"] is None
    assert data["total_likes"] is None
    assert data["total_comments"] is None
    assert data["posts_stats"][0]["metrics_status"] == "SIN_DATOS"
    assert data["posts_stats"][0]["views"] is None


# -----------------------------------------------------------------------------
# 7. Persistencia de video_id y ejecución de tareas programadas tras reinicio
# -----------------------------------------------------------------------------
def test_worker_executes_scheduled_job_after_restart_and_persists_video_id(tmp_path: Path, vertical_video_path: Path):
    db_file = tmp_path / "restart_persistence.db"
    db_url = f"sqlite:///{db_file.as_posix()}"

    # Sesión 1 (antes del reinicio): crear trabajo SCHEDULED listo para ejecutarse
    engine1 = create_engine(db_url)
    Base.metadata.create_all(bind=engine1)
    Session1 = sessionmaker(bind=engine1)
    s1 = Session1()
    src = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    s1.add(src)
    s1.flush()
    proj = Project(source_id=src.id, workflow_status="COMPLETED")
    s1.add(proj)
    s1.flush()
    rnd = Render(project_id=proj.id, target_platform="youtube", video_path=str(vertical_video_path))
    s1.add(rnd)
    s1.flush()
    draft = PostDraft(render_id=rnd.id, platform="youtube", title="Scheduled Before Restart", visibility="private")
    s1.add(draft)
    s1.flush()
    job = PublishJob(
        post_draft_id=draft.id,
        idempotency_key="restart-job-unique-1",
        status=JobStatus.SCHEDULED,
        scheduled_at_utc=utc_now() - timedelta(seconds=5),
    )
    s1.add(job)
    s1.commit()
    job_id = job.id
    s1.close()
    engine1.dispose()

    # Sesión 2 (tras reinicio): nuevo engine + nuevo worker procesa la tarea pendiente
    engine2 = create_engine(db_url)
    Session2 = sessionmaker(bind=engine2)
    s2 = Session2()
    pub = UnifiedPublisher(mock_mode=True)
    worker = PublishQueueWorker(Session2, pub)
    processed = worker.process_due_job(s2)
    assert processed is not None
    assert processed.id == job_id
    assert processed.status == JobStatus.PUBLISHED
    assert processed.remote_post_id is not None
    persisted_vid = processed.remote_post_id
    s2.close()
    engine2.dispose()

    # Sesión 3 (nuevo reinicio): verificar que remote_post_id sigue persistido y NO se re-ejecuta
    engine3 = create_engine(db_url)
    Session3 = sessionmaker(bind=engine3)
    s3 = Session3()
    reloaded = s3.query(PublishJob).filter(PublishJob.id == job_id).first()
    assert reloaded is not None
    assert reloaded.status == JobStatus.PUBLISHED
    assert reloaded.remote_post_id == persisted_vid
    worker3 = PublishQueueWorker(Session3, pub)
    assert worker3.process_due_job(s3) is None
    s3.close()
    engine3.dispose()


# -----------------------------------------------------------------------------
# 8. Supervisor arranca Web + Worker simultáneamente y Docker sin secretos fijos
# -----------------------------------------------------------------------------
def test_supervisor_builds_dual_processes_and_docker_has_no_default_secrets():
    procs = build_managed_processes(python_exe="python", host="127.0.0.1", port=8000, worker_interval=5.0)
    names = [p.name for p in procs]
    assert names == ["web-fastapi", "queue-worker"]

    compose_text = Path("docker-compose.publisher.yml").read_text(encoding="utf-8")
    assert "postiz_secure_password_2026" not in compose_text
    assert "change_this_jwt_secret" not in compose_text
    assert "default_secure_secret" not in compose_text
    assert ":latest" not in compose_text
    assert "127.0.0.1:8000:8000" in compose_text
    assert "127.0.0.1:4007:4007" in compose_text
    assert "7233:7233" not in compose_text
    assert "8088:8080" not in compose_text
