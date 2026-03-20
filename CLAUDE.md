# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the monitor (immediate fetch + hourly scheduler)
uv run main.py

# Re-discover RSS feeds from the Excel source file
uv run find_rss.py

# Add a new dependency
uv add <package>
```

## Environment Setup

Copy `.env.example` to `.env` and fill in:
- `DINGTALK_WEBHOOK_URL` / `DINGTALK_WEBHOOK_SECRET` — DingTalk robot credentials
- `GEMINI_API_KEY` — Google Gemini API key
- `GEMINI_MODEL` — defaults to `gemini-2.0-flash`
- `DB_PATH` — defaults to `./data/rss.db`

## Architecture

The pipeline runs on a `BlockingScheduler` (APScheduler) every hour:

```
fetch_all_feeds()  →  Storage.is_new() / save_article()  →  Taiwan filter  →  translate_articles()  →  send_to_dingtalk()
   (fetcher.py)           (storage.py)                      (main.py regex)     (translator.py)           (notifier.py)
```

**Key data flow:**
1. `fetcher.py` — async httpx fetches all 16 feeds concurrently; `feedparser` parses; returns `RawArticle` dataclasses
2. `storage.py` — SQLite dedup via SHA-256 of `link|title`; tracks `fetched_at` and `pushed_at`
3. `main.py` — regex `台湾|台灣|taiwan` filters articles for Taiwan relevance
4. `translator.py` — Gemini batch translates (5 articles/batch) to Simplified Chinese with 5–6 line summaries; falls back to per-article if batch fails
5. `notifier.py` — builds DingTalk Markdown payloads (splits at 18 000 chars), signs with HMAC-SHA256, POSTs

**Config (`config.py`):**
- `THINKTANK_FEEDS` — the 16 hardcoded RSS feeds (add/remove feeds here)
- `Settings` via `pydantic-settings` — reads `.env`, singleton via `get_settings()`

**Storage schema** (`articles` table): `id` (sha256[:16]), `source`, `title`, `summary`, `link`, `published_at`, `fetched_at`, `pushed_at`

## Known Issues

- FPRI and Al Jazeera Centre for Studies occasionally time out (network)
- Many think tank sites block simple User-Agents; fetcher uses a Chrome UA
- `find_rss.py` reads from `智库及高校名单.xlsx` to rediscover feeds

## Deployment

A systemd service unit is in `deploy/thinktank.service`. Update `WorkingDirectory` and `ExecStart` paths before installing.
