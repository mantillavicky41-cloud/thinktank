"""Configuration management using pydantic-settings."""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from source_registry import build_default_source_dicts


class RSSFeed(BaseModel):
    """A monitorable source, backed by RSS or HTML extraction."""

    name: str
    url: str
    category: str = "综合"
    kind: Literal["rss", "html"] = "rss"
    site_url: str = ""


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DingTalk
    dingtalk_webhook_url: str = ""
    dingtalk_webhook_secret: str = ""

    # Feishu (Lark)
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""

    # Gemini
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    # Database
    db_path: str = "./data/rss.db"

    # Logging
    log_level: str = "INFO"

    # Monitor sources — loaded from the spreadsheet, with RSS overlays when found
    rss_feeds: list[RSSFeed] = Field(
        default_factory=lambda: [RSSFeed(**f) for f in build_default_source_dicts()]
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton settings instance."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger."""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
