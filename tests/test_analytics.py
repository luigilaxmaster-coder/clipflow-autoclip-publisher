"""Pruebas unitarias para PerformanceAnalyzer y feedback loop de ClipFlow."""
from __future__ import annotations

import json
from pathlib import Path

from clipflow.analytics import PerformanceAnalyzer
from clipflow.metadata import MetadataGenerator


def test_deconstruct_viral_formula(tmp_path: Path):
    formula_file = tmp_path / "test_formulas.json"
    analyzer = PerformanceAnalyzer(formulas_path=formula_file)

    sample_videos = [
        {
            "video_id": "v1",
            "url": "https://youtube.com/watch?v=v1",
            "original_title": "TheGrefg en directo jugando GTA 6 momento increible #Shorts #GTA6 #TheGrefg",
            "views": 1000,
            "likes": 50,
            "comments": 10,
            "engagement_rate": 6.0,
        },
        {
            "video_id": "v2",
            "url": "https://youtube.com/watch?v=v2",
            "original_title": "Video normal sin impacto #Shorts",
            "views": 10,
            "likes": 0,
            "comments": 0,
            "engagement_rate": 0.0,
        },
    ]

    top_videos = [sample_videos[0]]
    formula = analyzer.deconstruct_viral_formula(top_videos, sample_videos)
    analyzer._save_formula(formula)

    assert formula_file.exists()
    assert "insights" in formula
    assert "#thegrefg" in formula["insights"]["winning_hashtags"] or "#gta6" in formula["insights"]["winning_hashtags"]


def test_metadata_generator_applies_formula(tmp_path: Path):
    formula_file = Path("data/viral_formulas.json")
    # Genera un título adaptativo usando la fórmula
    res = MetadataGenerator.apply_viral_formula(creator="TheGrefg", topic="GTA 6", hook_phrase="reacciona al escape")
    assert "TheGrefg" in res["title"]
    assert "#Shorts" in res["title"]
    assert len(res["title"]) <= 95
    # No debe contener emojis
    import re
    assert not re.search(r'[\U00010000-\U0010ffff]', res["title"])

