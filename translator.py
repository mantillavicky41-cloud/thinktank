"""LLM translation module using Google Gemini."""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from google import genai

from fetcher import RawArticle

logger = logging.getLogger(__name__)

# Maximum articles per batch translation request
_BATCH_SIZE = 5
# Retry settings
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2  # seconds
# Delay between batches to avoid rate limiting
_INTER_BATCH_DELAY = 1  # seconds


@dataclass
class TranslatedArticle:
    """Article with translated title and summary."""

    source: str
    category: str
    title_zh: str
    summary_zh: str
    link: str
    published_at: str | None = None


def _build_prompt(articles: list[RawArticle]) -> str:
    """Build a batch translation/summarization prompt for Taiwan-related articles."""
    items = []
    for i, a in enumerate(articles):
        items.append(
            f"[{i}]\n"
            f"title: {a.title}\n"
            f"summary: {a.summary}"
        )

    articles_text = "\n\n".join(items)
    return f"""你是一名专业的两岸关系与台湾问题研究员。以下是来自国际智库或高校的文章，均涉及台湾议题。

请对每篇文章完成以下任务：
1. 将标题翻译为简体中文（若已是中文则保留）
2. 将内容整理为5-6行详细摘要（简体中文），包含：核心论点、关键人物/机构、背景信息、对两岸关系的影响或含义

请严格以如下 JSON 数组格式返回，不要添加任何其他文字：
[
  {{"i": 0, "title_zh": "中文标题", "summary_zh": "详细中文摘要（5-6行）"}},
  ...
]

--- 以下是需要整理的文章 ---

{articles_text}"""


def _parse_response(text: str, count: int) -> list[dict]:
    """Parse the LLM JSON response, with fallback."""
    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        # Remove first and last lines (```json and ```)
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        logger.warning("Failed to parse LLM response as JSON")

    # Return empty dicts so the caller uses original text
    return [{}] * count


def _call_llm_with_retry(client, model: str, prompt: str) -> str | None:
    """Call LLM with retry and exponential backoff. Returns response text or None."""
    for attempt in range(_MAX_RETRIES):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
            )
            return response.text
        except Exception as e:
            wait = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "LLM call failed (attempt %d/%d): %s. Retrying in %ds...",
                attempt + 1, _MAX_RETRIES, e, wait,
            )
            if attempt < _MAX_RETRIES - 1:
                time.sleep(wait)
    return None


def _translate_single(client, model: str, article: RawArticle) -> dict:
    """Translate a single article as fallback. Returns dict with title_zh/summary_zh."""
    prompt = f"""你是一名专业新闻翻译员。请将以下新闻的标题和摘要直译为简体中文。
如果原文已经是中文，则保持原样。翻译要求准确、专业、简洁。
摘要部分请扩写为5-6行的详细摘要，涵盖新闻的核心事件、关键人物、背景信息和影响。

请严格以如下 JSON 格式返回，不要添加任何其他文字：
{{"title_zh": "中文标题", "summary_zh": "详细中文摘要（5-6行）"}}

title: {article.title}
summary: {article.summary}"""

    text = _call_llm_with_retry(client, model, prompt)
    if not text:
        return {}

    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [l for l in lines[1:] if not l.strip().startswith("```")]
        cleaned = "\n".join(lines)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        logger.warning("Failed to parse single-article LLM response")
    return {}


def translate_articles(
    articles: list[RawArticle],
    api_key: str,
    model: str = "gemini-2.5-flash-lite",
) -> list[TranslatedArticle]:
    """Translate a list of articles, returns TranslatedArticle list."""
    if not articles:
        return []

    client = genai.Client(api_key=api_key)
    translated: list[TranslatedArticle] = []

    # Process in batches
    for batch_start in range(0, len(articles), _BATCH_SIZE):
        batch = articles[batch_start : batch_start + _BATCH_SIZE]

        # Add delay between batches to avoid rate limiting
        if batch_start > 0:
            time.sleep(_INTER_BATCH_DELAY)

        # Check if all articles appear to be Chinese already
        all_chinese = all(_is_chinese(a.title) for a in batch)

        if all_chinese:
            for a in batch:
                translated.append(
                    TranslatedArticle(
                        source=a.source,
                        category=a.category,
                        title_zh=a.title,
                        summary_zh=a.summary,
                        link=a.link,
                        published_at=a.published_at,
                    )
                )
            continue

        # Try batch translation with retry
        prompt = _build_prompt(batch)
        response_text = _call_llm_with_retry(client, model, prompt)

        if response_text:
            results = _parse_response(response_text, len(batch))
        else:
            logger.warning("Batch translation failed after retries, falling back to individual translation")
            results = [{}] * len(batch)

        for j, a in enumerate(batch):
            r = results[j] if j < len(results) else {}

            # If this article has no translation, try individual fallback
            if not r.get("title_zh") and not _is_chinese(a.title):
                logger.info("Trying individual translation for: %s", a.title[:60])
                time.sleep(_INTER_BATCH_DELAY)
                r = _translate_single(client, model, a)

            title_zh = r.get("title_zh", "") or a.title
            summary_zh = r.get("summary_zh", "") or a.summary

            # Mark translation failure only if individual fallback also failed
            if not r.get("title_zh") and not _is_chinese(a.title):
                title_zh = f"[翻译失败] {a.title}"
                summary_zh = a.summary

            translated.append(
                TranslatedArticle(
                    source=a.source,
                    category=a.category,
                    title_zh=title_zh,
                    summary_zh=summary_zh,
                    link=a.link,
                    published_at=a.published_at,
                )
            )

    logger.info("Translated %d articles", len(translated))
    return translated


def _is_chinese(text: str) -> bool:
    """Rough check: if >50% characters are CJK, consider it Chinese."""
    if not text:
        return False
    cjk_count = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return cjk_count / len(text) > 0.5
