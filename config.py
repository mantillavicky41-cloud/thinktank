"""Configuration management using pydantic-settings."""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class RSSFeed(BaseModel):
    """A single RSS feed source."""

    name: str
    url: str
    category: str = "综合"


# ---------- Think Tank & University RSS Feeds ----------
# Discovered from 智库及高校名单.xlsx via find_rss.py

THINKTANK_FEEDS: list[dict[str, str]] = [
    # ---- 美国顶级智库 ----
    {
        "name": "Council on Foreign Relations (CFR)",
        "url": "https://www.cfr.org/feed",
        "category": "顶级智库-美国",
    },
    {
        "name": "CSIS (Center for Strategic and International Studies)",
        "url": "https://www.csis.org/rss.xml",
        "category": "顶级智库-美国",
    },
    {
        "name": "RAND Corporation",
        "url": "https://www.rand.org/news/rss.xml",
        "category": "顶级智库-美国",
    },
    {
        "name": "American Enterprise Institute (AEI)",
        "url": "https://www.aei.org/feed/",
        "category": "顶级智库-美国",
    },
    {
        "name": "Heritage Foundation",
        "url": "https://www.heritage.org/rss/",
        "category": "顶级智库-美国",
    },
    {
        "name": "Foreign Policy Research Institute (FPRI)",
        "url": "https://www.fpri.org/feed/",
        "category": "顶级智库-美国",
    },
    {
        "name": "Cato Institute",
        "url": "https://www.cato.org/feed",
        "category": "顶级智库-美国",
    },
    # ---- 欧洲顶级智库 ----
    {
        "name": "Bruegel",
        "url": "https://www.bruegel.org/rss.xml",
        "category": "顶级智库-欧洲",
    },
    {
        "name": "IFRI (French Institute of International Relations)",
        "url": "https://www.ifri.org/en/rss.xml",
        "category": "顶级智库-欧洲",
    },
    {
        "name": "ECFR (European Council on Foreign Relations)",
        "url": "https://ecfr.eu/feed/",
        "category": "顶级智库-欧洲",
    },
    {
        "name": "German Marshall Fund (GMF)",
        "url": "https://www.gmfus.org/rss.xml",
        "category": "顶级智库-欧洲",
    },
    {
        "name": "Clingendael Institute",
        "url": "https://www.clingendael.org/rss.xml",
        "category": "顶级智库-欧洲",
    },
    # ---- 中东重要智库 ----
    {
        "name": "Al Jazeera Centre for Studies",
        "url": "https://studies.aljazeera.net/rss.xml",
        "category": "重要智库-中东",
    },
    {
        "name": "BESA Center for Strategic Studies",
        "url": "https://besacenter.org/feed/",
        "category": "重要智库-中东",
    },
    {
        "name": "TIMEP (Tahrir Institute for Middle East Policy)",
        "url": "https://timep.org/feed/",
        "category": "重要智库-中东",
    },
    # ---- 重点院校 ----
    {
        "name": "Global Taiwan Institute (GTI)",
        "url": "https://globaltaiwan.org/feed/",
        "category": "重点院校",
    },
]


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

    # RSS Feeds — loaded from THINKTANK_FEEDS by default
    rss_feeds: list[RSSFeed] = [RSSFeed(**f) for f in THINKTANK_FEEDS]


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
