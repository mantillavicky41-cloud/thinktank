"""Feishu (Lark) custom-bot webhook notifier.

Mirrors notifier.py but speaks Feishu's protocol so we can push the same
Markdown content to both platforms in parallel and compare filtering.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

from notifier import split_markdown_segments, _now_cn_str
from reporter import PushResult, scan_sensitive
from translator import TranslatedArticle

logger = logging.getLogger(__name__)


def _sign(secret: str, timestamp: str) -> str:
    """Feishu signature: HMAC-SHA256 with key = f'{ts}\\n{secret}', empty msg."""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        string_to_sign.encode("utf-8"),
        b"",
        digestmod=hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def _mk_card(text: str, section_title: str, now_str: str) -> dict:
    """Wrap a Markdown text block in an interactive card payload."""
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"📰 {section_title} ({now_str})",
                },
                "template": "blue",
            },
            "elements": [
                {"tag": "markdown", "content": text},
            ],
        },
    }


def build_interactive_cards(
    articles: list[TranslatedArticle],
    section_title: str = "国际媒体快讯",
) -> tuple[list[dict], list[list[str]]]:
    """Build Feishu interactive-card payloads + per-message title lists.

    Shares split_markdown_segments with notifier.py so both platforms send
    byte-identical Markdown content.
    """
    now_str = _now_cn_str()
    segments = split_markdown_segments(articles, section_title, now_str)

    payloads: list[dict] = []
    titles_per_msg: list[list[str]] = []
    for text, titles in segments:
        payloads.append(_mk_card(text, section_title, now_str))
        titles_per_msg.append(titles)
    return payloads, titles_per_msg


def _extract_card_text(payload: dict) -> str:
    """Get the Markdown text we put into the card, for sensitive scan + len."""
    elements = payload.get("card", {}).get("elements", [])
    for el in elements:
        if el.get("tag") == "markdown":
            return el.get("content", "") or ""
    return ""


async def send_to_feishu(
    webhook_url: str,
    secret: str,
    cards: list[dict],
    titles_per_msg: list[list[str]] | None = None,
) -> list[PushResult]:
    """Send interactive cards to Feishu. Returns one PushResult per card."""
    if titles_per_msg is None:
        titles_per_msg = [[] for _ in cards]

    results: list[PushResult] = []
    total = len(cards)
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, card_payload in enumerate(cards, 1):
            timestamp = str(int(time.time()))
            sign = _sign(secret, timestamp)

            body = {
                "timestamp": timestamp,
                "sign": sign,
                **card_payload,
            }

            card_text = _extract_card_text(card_payload)
            hits = scan_sensitive(card_text)
            titles = titles_per_msg[i - 1] if i - 1 < len(titles_per_msg) else []

            errcode = -1
            errmsg = ""
            response_raw = ""
            try:
                resp = await client.post(webhook_url, json=body)
                response_raw = resp.text or ""
                try:
                    data = resp.json()
                    # Feishu returns top-level {code, msg} or {StatusCode, StatusMessage}.
                    # Prefer the modern lowercase schema; fall back to legacy.
                    if "code" in data:
                        errcode = int(data.get("code", -1))
                        errmsg = str(data.get("msg", ""))
                    else:
                        errcode = int(data.get("StatusCode", -1))
                        errmsg = str(data.get("StatusMessage", ""))
                except Exception:
                    errmsg = f"non-JSON response (HTTP {resp.status_code})"

                if errcode != 0:
                    logger.error("Feishu API error: %s", response_raw)
                else:
                    logger.info("Feishu message %d/%d sent", i, total)
            except Exception as e:
                errmsg = f"{type(e).__name__}: {e}"
                logger.exception("Failed to send Feishu message %d/%d", i, total)

            results.append(
                PushResult(
                    platform="feishu",
                    index=i,
                    total=total,
                    payload_len=len(card_text),
                    errcode=errcode,
                    errmsg=errmsg,
                    response_raw=response_raw,
                    sensitive_hits=hits,
                    article_titles=list(titles),
                )
            )

    return results
