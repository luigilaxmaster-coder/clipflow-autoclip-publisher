"""Pruebas del adaptador AutoClip y puente de variantes Studio (GATE-3)."""
from clipflow.autoclip_adapter import AutoClipAdapter


def test_extract_output_variants_normalization():
    adapter = AutoClipAdapter()
    raw_produce_result = {
        "ok": True,
        "project_id": "proj-12345",
        "status": "completed",
        "outputs": [
            {
                "id": "variant-yt-1",
                "strategy_id": "youtube_shorts",
                "status": "completed",
                "video_path": "C:/data/projects/proj-12345/output/studio/job1.mp4",
                "cover_path": "C:/data/projects/proj-12345/output/studio/cover1.jpg",
                "kit_path": "C:/data/projects/proj-12345/output/studio/publish-kits/variant-yt-1.zip",
                "post": {
                    "title": "La Clave del Éxito en AutoClip",
                    "description": "Video optimizado para YouTube Shorts.",
                    "tags": ["shorts", "tecnologia"],
                },
            },
            {
                "id": "variant-tt-1",
                "strategy_id": "tiktok",
                "status": "completed",
                "video_path": "C:/data/projects/proj-12345/output/studio/job2.mp4",
                "cover_path": "C:/data/projects/proj-12345/output/studio/cover2.jpg",
                "kit_path": None,
                "post": {
                    "title": "Tutorial Rápido TikTok",
                    "description": "Edición 9:16 podcast.",
                    "tags": ["fyp", "viral"],
                },
            },
        ],
    }

    variants = adapter.extract_output_variants(raw_produce_result)
    assert len(variants) == 2

    yt_var = variants[0]
    assert yt_var["output_variant_id"] == "variant-yt-1"
    assert yt_var["strategy_id"] == "youtube_shorts"
    assert yt_var["title"] == "La Clave del Éxito en AutoClip"
    assert yt_var["video_path"] == "C:/data/projects/proj-12345/output/studio/job1.mp4"

    tt_var = variants[1]
    assert tt_var["output_variant_id"] == "variant-tt-1"
    assert tt_var["strategy_id"] == "tiktok"
    assert tt_var["tags"] == ["fyp", "viral"]


def test_partial_status_preserves_completed_variants():
    adapter = AutoClipAdapter()
    partial_result = {
        "ok": True,
        "project_id": "proj-partial",
        "status": "partial",
        "outputs": [
            {
                "id": "variant-1",
                "strategy_id": "youtube_shorts",
                "status": "completed",
                "video_path": "C:/data/job1.mp4",
            },
            {
                "id": "variant-2",
                "strategy_id": "tiktok",
                "status": "failed",
                "video_path": None,
                "error": "Timeout during rendering",
            },
        ],
    }
    variants = adapter.extract_output_variants(partial_result)
    assert len(variants) == 2
    assert variants[0]["status"] == "completed"
    assert variants[1]["status"] == "failed"
