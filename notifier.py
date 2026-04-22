"""DingTalk webhook notifier module."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import httpx

from reporter import PushResult, scan_sensitive
from translator import TranslatedArticle

logger = logging.getLogger(__name__)

# DingTalk markdown message has a ~20000 char limit
_MAX_MSG_LEN = 18000


def _sign(secret: str) -> tuple[str, str]:
    """Generate DingTalk webhook signature.

    Returns (timestamp_str, sign_str).
    """
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(
        secret.encode("utf-8"),
        string_to_sign.encode("utf-8"),
        digestmod=hashlib.sha256,
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code).decode("utf-8"))
    return timestamp, sign


def build_markdown_message(
    articles: list[TranslatedArticle],
    section_title: str = "国际媒体快讯",
) -> tuple[list[dict], list[list[str]]]:
    """Build one or more DingTalk Markdown message payloads.

    Returns (payloads, titles_per_message) — titles_per_message[i] is the list
    of article title_zh included in payloads[i].
    """
    from datetime import datetime, timezone, timedelta

    tz_cn = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_cn).strftime("%Y-%m-%d %H:%M")

    header = f"### 📰 {section_title} ({now_str})\n\n"

    messages: list[dict] = []
    titles_per_msg: list[list[str]] = []
    current_text = header
    current_titles: list[str] = []

    for idx, a in enumerate(articles, 1):
        pub = a.published_at or "未知时间"
        entry = (
            f"**{idx}. {a.title_zh}**\n\n"
            f"> {a.summary_zh}\n\n"
            f"⏱ 时间: {pub} | 🏛 来源: {a.source}\n\n"
            f"[原文链接]({a.link})\n\n"
            f"---\n\n"
        )

        # If adding this entry would exceed the limit, flush current
        if len(current_text) + len(entry) > _MAX_MSG_LEN and current_titles:
            messages.append(_mk_payload(current_text, now_str, section_title))
            titles_per_msg.append(current_titles)
            current_text = header + f"*(续 第{len(messages)+1}部分)*\n\n"
            current_titles = []

        current_text += entry
        current_titles.append(a.title_zh)

    if current_titles:
        messages.append(_mk_payload(current_text, now_str, section_title))
        titles_per_msg.append(current_titles)

    return messages, titles_per_msg


def _mk_payload(text: str, time_str: str, section_title: str = "国际媒体快讯") -> dict:
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"📰 {section_title} ({time_str})",
            "text": text,
        },
    }


async def send_to_dingtalk(
    webhook_url: str,
    secret: str,
    messages: list[dict],
    titles_per_msg: list[list[str]] | None = None,
) -> list[PushResult]:
    """Send message(s) to DingTalk via webhook.

    Returns one PushResult per message (successful or not). Never raises.
    """
    if titles_per_msg is None:
        titles_per_msg = [[] for _ in messages]

    results: list[PushResult] = []
    total = len(messages)
    async with httpx.AsyncClient(timeout=30.0) as client:
        for i, payload in enumerate(messages, 1):
            timestamp, sign = _sign(secret)
            url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"

            payload_text = payload.get("markdown", {}).get("text", "")
            hits = scan_sensitive(payload_text)
            titles = titles_per_msg[i - 1] if i - 1 < len(titles_per_msg) else []

            errcode = -1
            errmsg = ""
            response_raw = ""
            try:
                resp = await client.post(url, json=payload)
                response_raw = resp.text or ""
                try:
                    data = resp.json()
                    errcode = int(data.get("errcode", -1))
                    errmsg = str(data.get("errmsg", ""))
                except Exception:
                    errmsg = f"non-JSON response (HTTP {resp.status_code})"

                if errcode != 0:
                    logger.error("DingTalk API error: %s", response_raw)
                else:
                    logger.info("DingTalk message %d/%d sent", i, total)
            except Exception as e:
                errmsg = f"{type(e).__name__}: {e}"
                logger.exception("Failed to send DingTalk message %d/%d", i, total)

            results.append(
                PushResult(
                    index=i,
                    total=total,
                    payload_len=len(payload_text),
                    errcode=errcode,
                    errmsg=errmsg,
                    response_raw=response_raw,
                    sensitive_hits=hits,
                    article_titles=list(titles),
                )
            )

    return results


async def send_test_message(webhook_url: str, secret: str) -> list[PushResult]:
    """Send a simple test message to verify webhook config."""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "🔧 RSS Bot 测试",
            "text": "### 🔧 RSS News Bot 测试消息\n\n"
            "如果你看到这条消息，说明钉钉 Webhook 配置正确！✅",
        },
    }
    return await send_to_dingtalk(webhook_url, secret, [payload], [["测试消息"]])
