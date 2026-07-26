from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WEDDING_QR_")

    anthropic_api_key: str = ""
    analysis_model: str = "claude-haiku-4-5"
    curation_model: str = "claude-haiku-4-5"

    data_dir: Path = Path(__file__).resolve().parent.parent.parent / "data"
    db_path: Path = data_dir / "wedding_qr.db"
    photos_dir: Path = data_dir / "photos"
    thumbnails_dir: Path = data_dir / "thumbnails"

    # Public base URL used to build the QR code's target link, e.g.
    # https://myevent.example.com — override in production.
    base_url: str = "http://localhost:8001"

    thumbnail_max_px: int = 480


def get_settings() -> Settings:
    return Settings()
