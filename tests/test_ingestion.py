"""Pruebas de ingesta y validación de seguridad SSRF (GATE-1)."""
from pathlib import Path
import pytest
from clipflow.ingestion import IngestionService, is_safe_remote_url


def test_ingest_local_video(vertical_video_path: Path):
    svc = IngestionService()
    source = svc.process_source(str(vertical_video_path))
    assert source.source_kind == "file"
    assert source.local_path == str(vertical_video_path)
    assert source.duration_sec > 0
    assert len(source.sha256) == 64


def test_ingest_invalid_extension(tmp_path: Path):
    bad_file = tmp_path / "document.pdf"
    bad_file.write_text("dummy")
    svc = IngestionService()
    with pytest.raises(ValueError, match="Formato de video no soportado"):
        svc.process_source(str(bad_file))


def test_ingest_missing_file():
    svc = IngestionService()
    with pytest.raises(FileNotFoundError):
        svc.process_source("C:/non_existent_video_path_12345.mp4")


def test_ssrf_and_url_security():
    # URL válida de YouTube
    safe, msg = is_safe_remote_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert safe is True

    # URL válida de Bilibili
    safe, msg = is_safe_remote_url("https://www.bilibili.com/video/BV1xx411c7mD")
    assert safe is True

    # Insegura (HTTP)
    safe, msg = is_safe_remote_url("http://www.youtube.com/watch?v=123")
    assert safe is False
    assert "HTTPS" in msg

    # Dominio no soportado
    safe, msg = is_safe_remote_url("https://malicious-site.com/exploit.mp4")
    assert safe is False
    assert "no soportado" in msg

    # SSRF: Localhost / IPs privadas
    safe, msg = is_safe_remote_url("https://localhost:8443/video.mp4")
    assert safe is False
