"""Pruebas unitarias e integradas para TikTok OAuth/Content Posting API, cuota YouTube e ingesta web."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

from clipflow.connections.providers import TikTokProvider
from clipflow.connections.service import ConnectionService
from clipflow.db import get_session, init_db
from clipflow.models import ConnectionStatus, JobStatus, PostDraft, Project, PublishJob, Render, Source
from clipflow.publishing.native_tiktok import NativeTikTokPublisher
from clipflow.publishing.unified import UnifiedPublisher
from clipflow.web.app import app


def test_tiktok_provider_requires_key_and_secret_for_oauth():
    provider = TikTokProvider()
    assert provider.info.required_fields == ["client_key", "client_secret"]
    ok, msg, diag = provider.test_connection({"client_key": "tk_key", "client_secret": "tk_sec"})
    assert ok is False
    assert diag.get("needs_oauth") is True
    assert "TikTok OAuth 2.0" in msg


def test_native_tiktok_oauth_url_and_inbox_fallback(tmp_path: Path):
    pub = NativeTikTokPublisher(
        client_key="aw_test_key",
        client_secret="aw_test_secret",
        access_token="act_test_token",
        redirect_uri="http://127.0.0.1:8000/api/connections/tiktok/callback",
    )
    auth_url = pub.get_authorization_url(state="state-123")
    assert "https://www.tiktok.com/v2/auth/authorize/" in auth_url
    assert "client_key=aw_test_key" in auth_url
    assert "state=state-123" in auth_url

    video = tmp_path / "clip.mp4"
    video.write_bytes(b"fake-mp4-payload-123456")

    init_direct_403 = MagicMock(
        status_code=403,
        text='{"error":{"code":"unaudited_client_can_only_post_to_private_accounts","message":"Unaudited"}}',
    )
    init_direct_403.json.return_value = {
        "error": {"code": "unaudited_client_can_only_post_to_private_accounts", "message": "Unaudited"}
    }
    init_inbox_ok = MagicMock(
        status_code=200,
        text='{"error":{"code":"ok"},"data":{"publish_id":"pub_inbox_1","upload_url":"https://upload.tiktok.example/1"}}',
    )
    init_inbox_ok.json.return_value = {
        "error": {"code": "ok"},
        "data": {"publish_id": "pub_inbox_1", "upload_url": "https://upload.tiktok.example/1"},
    }
    put_ok = MagicMock(status_code=201)

    with (
        patch("clipflow.publishing.native_tiktok.requests.request", side_effect=[init_direct_403, init_inbox_ok]),
        patch("clipflow.publishing.native_tiktok.requests.put", return_value=put_ok),
    ):
        res = pub.submit_post(
            video_path=str(video),
            title="Test Clip",
            platform="tiktok",
            profile="owner",
            visibility="private",
        )

    assert res.ok is True
    assert res.status == "ACTION_REQUIRED"
    assert res.remote_post_id == "pub_inbox_1"
    assert res.raw_payload and res.raw_payload.get("fallback_to_inbox") is True


def test_unified_publisher_routes_tiktok_to_native(vertical_video_path: Path):
    unified = UnifiedPublisher(mock_mode=True)
    pub = unified.select_publisher_for_platform("tiktok")
    assert isinstance(pub, NativeTikTokPublisher)
    res = unified.submit_post(
        video_path=str(vertical_video_path),
        title="TikTok Mock",
        platform="tiktok",
        profile="owner",
        visibility="SELF_ONLY",
    )
    assert res.ok is True
    assert res.status == "PUBLISHED"


def test_tiktok_oauth_start_and_callback_flow(monkeypatch, tmp_path: Path):
    token = "tiktok-oauth-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "tiktok_oauth.db"
    monkeypatch.setenv("CLIPFLOW_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.setattr("clipflow.config._active_settings", None)
    monkeypatch.setattr("clipflow.db._engine", None)
    monkeypatch.setattr("clipflow.db._SessionFactory", None)
    init_db()

    sess = get_session()
    try:
        ConnectionService(sess).save_connection(
            provider_id="tiktok",
            account_name="Mi Cuenta TikTok",
            credentials={"client_key": "tk_client_key_demo", "client_secret": "tk_client_secret_demo"},
            test_now=False,
        )
    finally:
        sess.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    start_res = client.get("/api/connections/tiktok/oauth/start", headers=headers)
    assert start_res.status_code == 200
    auth_url = start_res.json()["auth_url"]
    state_val = auth_url.split("state=")[1].split("&")[0]

    token_resp = MagicMock(status_code=200, text='{"access_token":"tt-act-123"}', headers={"Content-Type": "application/json"})
    token_resp.json.return_value = {
        "access_token": "tt-act-123",
        "refresh_token": "tt-ref-456",
        "expires_in": 86400,
        "open_id": "open_tt_99",
        "scope": "user.info.basic,video.upload",
    }
    user_resp = MagicMock(status_code=200)
    user_resp.json.return_value = {"data": {"user": {"open_id": "open_tt_99", "display_name": "GeistTikTok"}}}

    with (
        patch("clipflow.publishing.native_tiktok.requests.post", return_value=token_resp),
        patch("clipflow.connections.providers.requests.get", return_value=user_resp),
    ):
        cb_res = client.get(
            f"/api/connections/tiktok/callback?code=auth_code_1&state={state_val}",
            headers={"Authorization": f"Bearer {token}"},
            follow_redirects=False,
        )

    assert cb_res.status_code == 303
    assert "/connections?oauth=success&provider=tiktok" in cb_res.headers["location"]

    sess2 = get_session()
    try:
        conn = ConnectionService(sess2).get_connection("tiktok")
        assert conn is not None
        assert conn.status == ConnectionStatus.CONNECTED
        assert conn.account_name == "GeistTikTok"
        assert conn.account_id == "open_tt_99"
    finally:
        sess2.close()


def test_youtube_quota_snapshot_and_inbox_sync(monkeypatch, tmp_path: Path):
    token = "quota-inbox-owner-token-1234567890"
    monkeypatch.setenv("OWNER_TOKEN", token)
    db_file = tmp_path / "quota_inbox.db"
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
        draft = PostDraft(render_id=rnd.id, platform="youtube", title="Quota Video")
        sess.add(draft)
        sess.flush()
        job = PublishJob(
            post_draft_id=draft.id,
            idempotency_key="quota-yt-1",
            status=JobStatus.PUBLISHED,
            remote_post_id="YT_ID_1",
        )
        sess.add(job)
        sess.commit()
    finally:
        sess.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}"}
    status_res = client.get("/api/status", headers=headers)
    assert status_res.status_code == 200
    quota = status_res.json()["youtube_quota"]
    assert quota["used_uploads"] == 1
    assert quota["max_uploads"] == 6
    assert quota["remaining_uploads"] == 5

    sync_res = client.post("/api/inbox/sync", headers=headers, follow_redirects=False)
    assert sync_res.status_code == 303
    assert "/inbox?synced=" in sync_res.headers["location"]
