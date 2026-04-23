"""Source fetcher supporting RSS feeds and direct HTML pages."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser
import httpx

from config import RSSFeed
from html_source_extractor import clean_text, extract_html_articles
from reporter import FeedStat

logger = logging.getLogger(__name__)

# Timeout for individual feed requests
_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
}


@dataclass
class RawArticle:
    """A single article parsed from an RSS feed or HTML source."""

    source: str
    category: str
    title: str
    summary: str
    link: str
    published_at: str | None = None  # ISO format


def _parse_pub_date(entry: dict) -> str | None:
    """Try to extract a publication date from a feed entry."""
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if tp:
            try:
                dt = datetime(*tp[:6], tzinfo=timezone.utc)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    # Fallback: try parsing the string directly
    for key in ("published", "updated"):
        raw = entry.get(key, "")
        if raw:
            try:
                dt = parsedate_to_datetime(raw)
                return dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
    return None


def _clean_summary(raw: str) -> str:
    """Normalize and cap article summary text."""
    text = clean_text(raw)
    if len(text) > 1500:
        return text[:1497] + "..."
    return text


def _build_stat(feed: RSSFeed, t0: float, *, count: int, error: str | None) -> FeedStat:
    return FeedStat(
        name=feed.name,
        category=feed.category,
        article_count=count,
        duration_ms=int((time.monotonic() - t0) * 1000),
        error=error,
    )


async def _fetch_rss_source(
    client: httpx.AsyncClient,
    feed: RSSFeed,
) -> tuple[list[RawArticle], FeedStat]:
    """Fetch and parse one RSS/Atom feed."""
    t0 = time.monotonic()
    try:
        resp = await client.get(feed.url, follow_redirects=True)
        resp.raise_for_status()
        parsed = feedparser.parse(resp.text)
        articles: list[RawArticle] = []
        for entry in parsed.entries:
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            content_value = ""
            if "content" in entry and isinstance(entry.content, list) and len(entry.content) > 0:
                content_value = entry.content[0].get("value", "")

            summary_raw = content_value or entry.get("summary", "") or entry.get("description", "") or ""
            articles.append(
                RawArticle(
                    source=feed.name,
                    category=feed.category,
                    title=title,
                    summary=_clean_summary(summary_raw),
                    link=link,
                    published_at=_parse_pub_date(entry),
                )
            )

        logger.info("Fetched %d articles from RSS source %s", len(articles), feed.name)
        return articles, _build_stat(feed, t0, count=len(articles), error=None)
    except Exception as e:
        logger.warning("Failed to fetch RSS source %s (%s)", feed.name, feed.url, exc_info=True)
        return [], _build_stat(feed, t0, count=0, error=f"{type(e).__name__}: {e}"[:200])


async def _fetch_html_source(
    client: httpx.AsyncClient,
    feed: RSSFeed,
) -> tuple[list[RawArticle], FeedStat]:
    """Fetch and parse a direct HTML source page."""
    t0 = time.monotonic()
    try:
        resp = await client.get(feed.url, follow_redirects=True)
        resp.raise_for_status()

        extracted = extract_html_articles(resp.text, str(resp.url))
        articles = [
            RawArticle(
                source=feed.name,
                category=feed.category,
                title=str(item["title"]),
                summary=_clean_summary(str(item.get("summary") or "")),
                link=str(item["link"]),
                published_at=item.get("published_at"),
            )
            for item in extracted
            if item.get("title") and item.get("link")
        ]

        logger.info("Fetched %d articles from HTML source %s", len(articles), feed.name)
        return articles, _build_stat(feed, t0, count=len(articles), error=None)
    except Exception as e:
        logger.warning("Failed to fetch HTML source %s (%s)", feed.name, feed.url, exc_info=True)
        return [], _build_stat(feed, t0, count=0, error=f"{type(e).__name__}: {e}"[:200])


async def fetch_feed(
    client: httpx.AsyncClient,
    feed: RSSFeed,
) -> tuple[list[RawArticle], FeedStat]:
    """Fetch and parse one configured source.

    Returns a tuple of (articles, stat). On failure, articles is [] and
    stat.error is a short error string.
    """
    if feed.kind == "html":
        return await _fetch_html_source(client, feed)
    return await _fetch_rss_source(client, feed)


async def fetch_all_feeds(
    feeds: list[RSSFeed],
) -> tuple[list[RawArticle], list[FeedStat]]:
    """Fetch all configured sources concurrently.

    Returns (all_articles_sorted, per_feed_stats).
    """
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        tasks = [fetch_feed(client, f) for f in feeds]
        results = await asyncio.gather(*tasks)

    all_articles: list[RawArticle] = []
    stats: list[FeedStat] = []
    for batch, stat in results:
        all_articles.extend(batch)
        stats.append(stat)

    # Sort by published_at descending (newest first), unknowns at end
    all_articles.sort(
        key=lambda a: a.published_at or "0000-00-00 00:00", reverse=True
    )
    logger.info("Total articles fetched: %d", len(all_articles))
    return all_articles, stats
