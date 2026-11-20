"""Pruebas del conector nativo directo de YouTube Data API v3 y Google OAuth 2.0."""
import pytest
from pathlib import Path
from clipflow.publishing.native_youtube import NativeYouTubePublisher


def test_native_youtube_oauth_and_upload(vertical_video_path: Path):
    yt = NativeYouTubePublisher(
        client_id="mock-client-id",
        client_secret="mock-client-secret",
        mock_mode=True,
    )
    assert yt.is_configured is True

    # 1. URL de autorización OAuth 2.0
    auth_url = yt.get_authorization_url(state="secure_state_123")
    assert "accounts.google.com/o/oauth2/v2/auth" in auth_url
    assert "youtube.upload" in auth_url
    assert "secure_state_123" in auth_url

    # 2. Canje de código por tokens
    token_data = yt.exchange_code("mock_auth_code")
    assert "access_token" in token_data
    assert "refresh_token" in token_data

    # 3. Renovación de token de acceso
    refreshed = yt.refresh_access_token()
    assert refreshed.startswith("refreshed-mock-token-")

    # 4. Listar canal y verificar cuenta
    profiles = yt.list_profiles()
    assert len(profiles) == 1
    assert yt.verify_account("OwnerYouTubeChannel", "youtube") is True

    # 5. Subida de video con límite estricto de 100 caracteres
    long_title = "A" * 150
    res = yt.submit_post(
        video_path=str(vertical_video_path),
        title=long_title,
        platform="youtube",
        profile="OwnerYouTubeChannel",
        description="Descripción con #Shorts",
        visibility="unlisted",
    )
    assert res.ok is True
    assert res.status == "PUBLISHED"
    assert "youtube.com/shorts/" in res.remote_url

    # 6. Consultar estado del video
    status_res = yt.get_status(res.job_id)
    assert status_res.ok is True
    assert status_res.status == "PUBLISHED"

    # 7. Comentarios y respuestas oficiales
    comments = yt.fetch_comments(res.job_id)
    assert len(comments) >= 1
    assert yt.reply_to_comment(comments[0]["comment_id"], "Gracias por tu apoyo!") is True

    # 8. Cancelar video
    assert yt.cancel_scheduled(res.job_id) is True
