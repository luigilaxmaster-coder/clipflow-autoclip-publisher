"""Pruebas de publicación simulada con Upload-Post (GATE-4)."""
from pathlib import Path
from clipflow.publishing.upload_post import UploadPostPublisher


def test_upload_post_mock_workflow(vertical_video_path: Path):
    publisher = UploadPostPublisher(mock_mode=True)
    assert publisher.is_configured is True

    # 1. Enviar post de YouTube en modo privado
    res_yt = publisher.submit_post(
        video_path=str(vertical_video_path),
        title="Prueba Unitaria YouTube Shorts",
        platform="youtube",
        profile="main",
        visibility="private",
    )
    assert res_yt.ok is True
    assert res_yt.request_id.startswith("mock-req-")
    assert res_yt.status == "REMOTE_PROCESSING"

    # 2. Consultar estado remoto
    status_yt = publisher.get_status(res_yt.request_id)
    assert status_yt.ok is True
    assert status_yt.status == "PUBLISHED"
    assert "mock.youtube.com" in (status_yt.remote_url or "")

    # 3. Enviar post de TikTok en modo SELF_ONLY
    res_tt = publisher.submit_post(
        video_path=str(vertical_video_path),
        title="Prueba Unitaria TikTok",
        platform="tiktok",
        profile="main",
        visibility="SELF_ONLY",
    )
    assert res_tt.ok is True
    status_tt = publisher.get_status(res_tt.request_id)
    assert status_tt.status == "PUBLISHED"
    assert "mock.tiktok.com" in (status_tt.remote_url or "")


def test_unconfigured_publisher_blocks_real_submit(vertical_video_path: Path):
    publisher = UploadPostPublisher(api_key="", mock_mode=False)
    assert publisher.is_configured is False

    res = publisher.submit_post(
        video_path=str(vertical_video_path),
        title="Intento no autorizado",
        platform="youtube",
        profile="main",
    )
    assert res.ok is False
    assert res.status == "FAILED"
    assert "UPLOAD_POST_API_KEY" in (res.error or "")
