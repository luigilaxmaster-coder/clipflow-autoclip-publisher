"""Pruebas de validación multimedia con FFprobe (GATE-2)."""
from pathlib import Path
from clipflow.media_qa import MediaQA


def test_qa_vertical_video_passes(media_qa: MediaQA, vertical_video_path: Path):
    assert vertical_video_path.exists()
    report = media_qa.validate_clip(
        vertical_video_path,
        platform="youtube_shorts",
        min_duration_sec=2.0,
        max_duration_sec=60.0,
        require_audio=True,
        require_vertical=True,
    )
    assert report.passed is True
    assert report.overall_status in ("PASSED", "WARNING")
    assert report.width == 1080
    assert report.height == 1920
    assert report.video_codec == "h264"
    assert report.audio_codec == "aac"
    assert report.duration_sec > 4.0


def test_qa_empty_file_fails(media_qa: MediaQA, empty_video_path: Path):
    report = media_qa.validate_clip(empty_video_path)
    assert report.passed is False
    assert report.overall_status == "FAILED"
    fail_rules = [r for r in report.rules if r.status == "fail"]
    assert any(r.rule == "file_not_empty" for r in fail_rules)


def test_qa_corrupt_file_fails(media_qa: MediaQA, corrupt_video_path: Path):
    report = media_qa.validate_clip(corrupt_video_path)
    assert report.passed is False
    assert report.overall_status == "FAILED"


def test_qa_horizontal_video_fails_vertical_rule(media_qa: MediaQA, horizontal_video_path: Path):
    report = media_qa.validate_clip(
        horizontal_video_path,
        require_vertical=True,
        min_duration_sec=1.0,
    )
    assert report.passed is False
    assert report.overall_status == "FAILED"
    fail_rules = [r for r in report.rules if r.status == "fail"]
    assert any(r.rule == "aspect_ratio_9_16" for r in fail_rules)


def test_qa_missing_file_fails(media_qa: MediaQA, tmp_path: Path):
    missing = tmp_path / "does_not_exist.mp4"
    report = media_qa.validate_clip(missing)
    assert report.passed is False
    assert report.overall_status == "FAILED"
