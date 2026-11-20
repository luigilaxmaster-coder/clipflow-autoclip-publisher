"""Pruebas del adaptador desacoplado para el motor de publicación Postiz."""
import pytest
from pathlib import Path
from clipflow.publishing.postiz import PostizPublisher


def test_postiz_publisher_mock_mode():
    publisher = PostizPublisher(mock_mode=True)
    assert publisher.is_configured is True
    assert publisher.ping() is True

    # 1. Listar integraciones conectadas
    profiles = publisher.list_profiles()
    assert len(profiles) >= 2
    platforms = [cp for p in profiles for cp in p.get("connected_platforms", [])]
    assert "youtube" in platforms
    assert "tiktok" in platforms

    # 2. Verificar cuenta
    assert publisher.verify_account("personal_owner", "youtube") is True

    # 3. Publicación inmediata
    res = publisher.submit_post(
        video_path="tests/fixtures/test_vertical_9_16.mp4",
        title="Postiz Test Video",
        platform="youtube",
        profile="personal_owner",
        description="Testing Postiz adapter",
        visibility="private",
    )
    assert res.ok is True
    assert res.status == "PUBLISHED"
    assert res.remote_url is not None

    # 4. Consultar estado remoto
    status_res = publisher.get_status(res.request_id)
    assert status_res.ok is True
    assert status_res.status == "PUBLISHED"

    # 5. Programar y cancelar
    sched_res = publisher.submit_post(
        video_path="tests/fixtures/test_vertical_9_16.mp4",
        title="Scheduled Postiz Video",
        platform="tiktok",
        profile="personal_owner",
        scheduled_date_iso="2026-12-01T15:00:00Z",
    )
    assert sched_res.ok is True
    assert publisher.cancel_scheduled(sched_res.request_id) is True
