"""Pruebas de sincronización de la biblioteca multimedia y publicación unificada."""
import pytest
from pathlib import Path
from sqlalchemy.orm import Session

from clipflow.media_library import MediaLibraryService
from clipflow.models import MediaAsset, Project, Render, Source
from clipflow.publishing.unified import UnifiedPublisher


def test_media_library_sync(db_session: Session, vertical_video_path: Path):
    # 1. Crear proyecto y render en BD simulando AutoClip
    source = Source(source_kind="file", canonical_source=str(vertical_video_path), local_path=str(vertical_video_path))
    db_session.add(source)
    db_session.flush()

    project = Project(source_id=source.id, workflow_status="COMPLETED")
    db_session.add(project)
    db_session.flush()

    render = Render(
        project_id=project.id,
        target_platform="youtube_shorts",
        video_path=str(vertical_video_path),
        width=1080,
        height=1920,
        duration_sec=15.0,
        qa_status="PASSED",
    )
    db_session.add(render)
    db_session.commit()

    # 2. Sincronizar a Media Library
    media_service = MediaLibraryService(db_session)
    synced = media_service.sync_renders_from_autoclip()
    assert synced == 1

    # 3. Comprobar que existe el asset sin duplicar el archivo
    assets = media_service.list_assets()
    assert len(assets) == 1
    assert assets[0].render_id == render.id
    assert assets[0].file_path == str(vertical_video_path)

    # Re-sincronizar no debe duplicar registros
    assert media_service.sync_renders_from_autoclip() == 0


def test_unified_publisher_dispatch(vertical_video_path: Path):
    unified = UnifiedPublisher(mock_mode=True)

    # 1. Despachar a YouTube
    res_yt = unified.submit_post(
        video_path=str(vertical_video_path),
        title="Unified YouTube Test",
        platform="youtube",
        profile="personal_owner",
        visibility="private",
    )
    assert res_yt.ok is True
    assert res_yt.status == "PUBLISHED"

    # 2. Despachar a TikTok
    res_tt = unified.submit_post(
        video_path=str(vertical_video_path),
        title="Unified TikTok Test",
        platform="tiktok",
        profile="personal_owner",
        visibility="SELF_ONLY",
    )
    assert res_tt.ok is True
    assert res_tt.status == "PUBLISHED"

    # 3. Consultar estados
    assert unified.get_status(res_yt.request_id).ok is True
    assert unified.get_status(res_tt.request_id).ok is True
