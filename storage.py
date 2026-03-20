"""SQLite persistence layer for article de-duplication."""

from __future__ import annotations

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _article_id(link: str, title: str) -> str:
    """Generate a deterministic ID from link + title."""
    raw = f"{link}|{title}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class Storage:
    """Thin wrapper around SQLite for article tracking."""

    def __init__(self, db_path: str) -> None:
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id          TEXT PRIMARY KEY,
                source      TEXT NOT NULL,
                title       TEXT NOT NULL,
                summary     TEXT,
                link        TEXT NOT NULL,
                published_at TEXT,
                fetched_at  TEXT NOT NULL,
                pushed_at   TEXT
            )
        """)
        self.conn.commit()

    # ---- public API ----

    def is_new(self, link: str, title: str) -> bool:
        """Return True if this article hasn't been stored yet."""
        aid = _article_id(link, title)
        row = self.conn.execute(
            "SELECT 1 FROM articles WHERE id = ?", (aid,)
        ).fetchone()
        return row is None

    def save_article(
        self,
        *,
        source: str,
        title: str,
        summary: str,
        link: str,
        published_at: str | None,
    ) -> str:
        """Insert a new article and return its ID."""
        aid = _article_id(link, title)
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO articles
                   (id, source, title, summary, link, published_at, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (aid, source, title, summary, link, published_at, now),
            )
            self.conn.commit()
        except sqlite3.Error:
            logger.exception("Failed to save article %s", aid)
        return aid

    def mark_pushed(self, article_ids: list[str]) -> None:
        """Mark articles as pushed."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "UPDATE articles SET pushed_at = ? WHERE id = ?",
            [(now, aid) for aid in article_ids],
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
