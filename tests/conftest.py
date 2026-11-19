"""Configuración global y fixtures para pytest."""
from __future__ import annotations

from pathlib import Path
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from clipflow.models import Base
from clipflow.media_qa import MediaQA

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture
def vertical_video_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "test_vertical_9_16.mp4"


@pytest.fixture
def horizontal_video_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "test_horizontal_16_9.mp4"


@pytest.fixture
def empty_video_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "empty.mp4"


@pytest.fixture
def corrupt_video_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "corrupt.mp4"


@pytest.fixture
def srt_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "test.srt"


@pytest.fixture
def db_session(tmp_path: Path):
    db_file = tmp_path / "test.db"
    engine = create_engine(f"sqlite:///{db_file.as_posix()}", echo=False)
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def media_qa() -> MediaQA:
    return MediaQA()
