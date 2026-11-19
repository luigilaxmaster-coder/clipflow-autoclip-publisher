"""Pruebas unitarias para LiveStreamRadar y detección de directos."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from clipflow.radar import LiveStreamRadar


def test_radar_initialization(tmp_path: Path):
    hist_file = tmp_path / "radar_hist.json"
    radar = LiveStreamRadar(history_file=hist_file)
    assert radar.history == {"processed_streams": {}, "daily_count": {}}


def test_radar_ignores_already_processed_stream(tmp_path: Path):
    hist_file = tmp_path / "radar_hist.json"
    radar = LiveStreamRadar(history_file=hist_file)
    radar.history["processed_streams"]["video_123"] = "2026-11-19T00:00:00Z"
    radar._save_history()

    # Si ya fue procesado, check_channel_live debe retornar None
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout='{"id": "video_123", "is_live": true}')
        res = radar.check_channel_live("TestCreator", "https://youtube.com/@test/live")
        assert res is None
