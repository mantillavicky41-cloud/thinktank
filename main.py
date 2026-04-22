"""Think Tank RSS Monitor — Main entry point.

Fetches RSS feeds from think tanks/universities every hour,
saves all articles to SQLite, and pushes Taiwan-related articles
to DingTalk after LLM formatting.
"""

from __future__ import annotations

import asyncio
import logging
import re
import signal
import sys
from collections import Counter
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from config import get_settings, setup_logging
from fetcher import RawArticle, fetch_all_feeds
from notifier import build_markdown_message, send_to_dingtalk
from reporter import CycleReporter
from storage import Storage
from translator import translate_articles

logger = logging.getLogger(__name__)

# Keywords that flag an article as Taiwan-related
_TAIWAN_KEYWORDS = re.compile(r"台湾|台灣|taiwan", re.IGNORECASE)


def _is_taiwan_related(article: RawArticle) -> bool:
    """Return True if the article mentions Taiwan in title or summary."""
    text = f"{article.title} {article.summary}"
    return bool(_TAIWAN_KEYWORDS.search(text))


def _translate_and_push(
    articles: list[RawArticle],
    article_ids: list[str],
    *,
    label: str,
    settings,
    storage: Storage,
    reporter: CycleReporter,
) -> int:
    """Translate a batch of articles and push to DingTalk.

    Returns the number of articles pushed.
    """
    if not articles:
        return 0

    translated, trans_stats = translate_articles(
        articles,
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
    )
    reporter.record_translation(trans_stats)

    messages, titles_per_msg = build_markdown_message(translated, section_title=label)
    logger.info(
        "[%s] Pushing %d articles in %d message(s)",
        label, len(translated), len(messages),
    )
    push_results = asyncio.run(
        send_to_dingtalk(
            settings.dingtalk_webhook_url,
            settings.dingtalk_webhook_secret,
            messages,
            titles_per_msg,
        )
    )
    reporter.record_push(push_results)

    storage.mark_pushed(article_ids)
    return len(translated)


def run_job() -> None:
    """Execute one fetch-store-filter-push cycle.

    1. Fetch all think tank RSS feeds.
    2. Save every new article to the database.
    3. Filter articles mentioning Taiwan (台湾/taiwan).
    4. Use LLM to format Taiwan articles as title+summary+time+source.
    5. Push formatted articles to DingTalk.
    """
    settings = get_settings()
    storage = Storage(settings.db_path)
    reporter = CycleReporter(Path(settings.db_path).parent / "reports")
    reporter.start()

    try:
        logger.info("=== Starting RSS fetch cycle ===")
        articles, feed_stats = asyncio.run(fetch_all_feeds(settings.rss_feeds))
        reporter.record_feeds(feed_stats)

        if not articles:
            logger.info("No articles fetched, skipping.")
            reporter.record_new_articles(0, 0, {})
            reporter.record_taiwan_hits([])
            return

        # Filter out already-seen articles
        new_articles: list[RawArticle] = []
        for a in articles:
            if storage.is_new(a.link, a.title):
                new_articles.append(a)

        per_source_new: dict[str, int] = dict(
            Counter(a.source for a in new_articles)
        )
        reporter.record_new_articles(
            new_count=len(new_articles),
            total_count=len(articles),
            per_source=per_source_new,
        )

        logger.info(
            "Fetched %d total, %d are new", len(articles), len(new_articles)
        )

        if not new_articles:
            logger.info("No new articles.")
            reporter.record_taiwan_hits([])
            return

        # Save ALL new articles to DB for deduplication & archiving
        article_ids: list[str] = []
        for a in new_articles:
            aid = storage.save_article(
                source=a.source,
                title=a.title,
                summary=a.summary,
                link=a.link,
                published_at=a.published_at,
            )
            article_ids.append(aid)

        logger.info("Saved %d new articles to database", len(new_articles))

        # Filter for Taiwan-related articles
        tw_articles: list[RawArticle] = []
        tw_ids: list[str] = []
        for a, aid in zip(new_articles, article_ids):
            if _is_taiwan_related(a):
                tw_articles.append(a)
                tw_ids.append(aid)

        reporter.record_taiwan_hits([(a.source, a.title) for a in tw_articles])

        logger.info(
            "%d / %d new articles mention Taiwan",
            len(tw_articles), len(new_articles),
        )

        # Push Taiwan articles via LLM + DingTalk
        pushed = _translate_and_push(
            tw_articles, tw_ids,
            label="智库台湾议题快报",
            settings=settings, storage=storage,
            reporter=reporter,
        )

        logger.info("=== Cycle complete: %d Taiwan articles pushed ===", pushed)

    except Exception as e:
        reporter.record_error("run_job", e)
        logger.exception("Error during fetch cycle")
    finally:
        reporter.finalize()
        storage.close()


def main() -> None:
    settings = get_settings()
    setup_logging(settings.log_level)

    logger.info("Think Tank RSS Monitor starting...")
    logger.info("Push schedule: every hour on the hour")
    logger.info("RSS feeds configured: %d", len(settings.rss_feeds))

    if not settings.dingtalk_webhook_url:
        logger.error("DINGTALK_WEBHOOK_URL is not set!")
        sys.exit(1)
    if not settings.dingtalk_webhook_secret:
        logger.error("DINGTALK_WEBHOOK_SECRET is not set!")
        sys.exit(1)
    if not settings.gemini_api_key:
        logger.error("GEMINI_API_KEY is not set!")
        sys.exit(1)

    logger.info("Running initial fetch cycle...")
    run_job()

    scheduler = BlockingScheduler()
    scheduler.add_job(run_job, "cron", minute=0, id="hourly_push")

    def shutdown(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    logger.info("Scheduler started. Fetching and filtering every hour.")
    scheduler.start()


if __name__ == "__main__":
    main()
