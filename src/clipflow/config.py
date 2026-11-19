"""Configuración central para clipflow."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, List, Optional
import yaml
from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass


class AppConfig(BaseModel):
    data_dir: str = Field(default="")
    timezone: str = "America/Santo_Domingo"
    log_level: str = "INFO"
    max_concurrent_video_jobs: int = 1
    max_concurrent_uploads: int = 1
    retain_source_days: int = 30
    database_url: str = ""


class AnalysisConfig(BaseModel):
    max_clips_per_source: int = 10
    target_clip_duration_sec: List[int] = Field(default_factory=lambda: [30, 60])
    min_quality_score: float = 0.6
    min_spacing_between_selected_clips_sec: float = 8.0
    transcription: str = "existing_srt_then_local_or_configured_cloud"
    analysis_provider: str = "autoclip_saved_settings"


class RenderConfig(BaseModel):
    platforms: List[str] = Field(default_factory=lambda: ["youtube_shorts", "tiktok"])
    portrait_style: str = "podcast"
    template: str = "classic"
    aspect_ratio: str = "9:16"
    subtitles: bool = True
    brand_outro: bool = False
    validate_mp4: bool = True


class PublishingConfig(BaseModel):
    provider: str = "upload_post"
    profile: str = "main"
    mode: str = "draft_only"  # draft_only | private_test | manual | auto_approved
    public_autopublish: bool = False
    default_visibility: dict[str, str] = Field(
        default_factory=lambda: {"youtube": "private", "tiktok": "SELF_ONLY"}
    )
    schedule_timezone: str = "America/Santo_Domingo"
    max_publishes_per_day_per_platform: int = 2
    retry_attempts: int = 3
    retry_backoff_sec: float = 5.0
    require_account_preflight: bool = True
    require_qa_pass: bool = True
    deduplicate_uploads: bool = True


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    publishing: PublishingConfig = Field(default_factory=PublishingConfig)

    @classmethod
    def load(cls, config_path: Optional[str | Path] = None) -> Settings:
        """Carga la configuración desde YAML con overrides de variables de entorno."""
        data: dict[str, Any] = {}

        # Determinar ruta de archivo de configuración
        if config_path and Path(config_path).exists():
            cfg_file = Path(config_path)
        else:
            default_paths = [
                Path("config/settings.yaml"),
                Path("config/settings.example.yaml"),
                Path(__file__).resolve().parent.parent.parent / "config" / "settings.yaml",
                Path(__file__).resolve().parent.parent.parent / "config" / "settings.example.yaml",
            ]
            cfg_file = next((p for p in default_paths if p.exists()), None)

        if cfg_file:
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    loaded = yaml.safe_load(f)
                    if isinstance(loaded, dict):
                        data = loaded
            except Exception:
                pass

        settings = cls(**data)

        # Overrides por variables de entorno
        if os.getenv("CLIPFLOW_DATA_DIR"):
            settings.app.data_dir = os.environ["CLIPFLOW_DATA_DIR"]
        elif not settings.app.data_dir:
            settings.app.data_dir = str(
                Path(os.getenv("APPDATA", "~/.clipflow")).expanduser().resolve() / "AutoClipPublisher"
            )

        if os.getenv("CLIPFLOW_TIMEZONE"):
            settings.app.timezone = os.environ["CLIPFLOW_TIMEZONE"]
            settings.publishing.schedule_timezone = os.environ["CLIPFLOW_TIMEZONE"]

        if os.getenv("CLIPFLOW_LOG_LEVEL"):
            settings.app.log_level = os.environ["CLIPFLOW_LOG_LEVEL"]

        if os.getenv("CLIPFLOW_DATABASE_URL"):
            settings.app.database_url = os.environ["CLIPFLOW_DATABASE_URL"]
        elif not settings.app.database_url:
            db_path = Path(settings.app.data_dir) / "clipflow.db"
            settings.app.database_url = f"sqlite:///{db_path.as_posix()}"

        if os.getenv("UPLOAD_POST_USER"):
            settings.publishing.profile = os.environ["UPLOAD_POST_USER"]

        # Crear directorio de datos si no existe
        Path(settings.app.data_dir).mkdir(parents=True, exist_ok=True)

        return settings


_active_settings: Optional[Settings] = None


def get_settings(config_path: Optional[str | Path] = None) -> Settings:
    global _active_settings
    if _active_settings is None or config_path is not None:
        _active_settings = Settings.load(config_path)
    return _active_settings
