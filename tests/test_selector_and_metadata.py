"""Pruebas de selección, ranking, descarte y generación de metadatos (RF-02 y RF-05)."""
import json
from clipflow.metadata import MetadataGenerator, sanitize_text
from clipflow.selector import ClipSelector


def test_clip_selector_ranking_and_quota():
    selector = ClipSelector(max_clips=2, min_score=0.6, min_spacing_sec=5.0)

    raw_candidates = [
        {"start_ms": 0, "end_ms": 30000, "score": 0.95, "title": "Top Segment 1"},
        {"start_ms": 20000, "end_ms": 40000, "score": 0.88, "title": "Overlapping Segment"},
        {"start_ms": 60000, "end_ms": 90000, "score": 0.82, "title": "Top Segment 2"},
        {"start_ms": 120000, "end_ms": 150000, "score": 0.40, "title": "Low Quality Segment"},
        {"start_ms": 180000, "end_ms": 210000, "score": 0.75, "title": "Excess Quota Segment"},
    ]

    selected = selector.select_candidates(raw_candidates, project_id="proj-test")
    # Total candidates returned with statuses
    assert len(selected) == 5

    accepted = [c for c in selected if c.selection_status == "selected"]
    assert len(accepted) == 2
    assert accepted[0].score == 0.95
    assert accepted[1].score == 0.82

    # Overlapping segment marked as duplicate
    duplicates = [c for c in selected if c.selection_status == "duplicate"]
    assert len(duplicates) == 1
    assert "solapamiento" in duplicates[0].reason

    # Low score segment dropped
    dropped = [c for c in selected if c.selection_status == "dropped"]
    assert len(dropped) == 2


def test_metadata_generator_platform_rules():
    meta = MetadataGenerator()
    drafts = meta.generate_drafts(
        render_id="render-123",
        base_title="Descubre Cómo Funciona la Automatización de Video en Redes Sociales con Inteligencia Artificial y Modelos Locales en Windows 11",
        base_description="Explicación detallada paso a paso del pipeline.",
        tags=["AutoClip", "VideoAI", "Automation"],
        visibility_yt="private",
        visibility_tt="SELF_ONLY",
    )

    assert len(drafts) == 2
    yt = next(d for d in drafts if d.platform == "youtube")
    tt = next(d for d in drafts if d.platform == "tiktok")

    # YouTube: Max 100 chars for title, #Shorts in description
    assert len(yt.title) <= 100
    assert "#Shorts" in yt.description
    assert yt.visibility == "private"

    # TikTok: Caption within limit, SELF_ONLY visibility
    assert len(tt.title) <= 150
    assert len(tt.description) <= 2200
    assert tt.visibility == "SELF_ONLY"
    assert "#fyp" in tt.description
