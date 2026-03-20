"""DingTalk webhook notifier module."""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
import urllib.parse

import httpx

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
) -> list[dict]:
    """Build one or more DingTalk Markdown message payloads.

    Splits into multiple messages if content exceeds the length limit.
    """
    from datetime import datetime, timezone, timedelta

    tz_cn = timezone(timedelta(hours=8))
    now_str = datetime.now(tz_cn).strftime("%Y-%m-%d %H:%M")

    header = f"### 📰 {section_title} ({now_str})\n\n"

    messages: list[dict] = []
    current_text = header
    current_count = 0

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
        if len(current_text) + len(entry) > _MAX_MSG_LEN and current_count > 0:
            messages.append(_mk_payload(current_text, now_str, section_title))
            current_text = header + f"*(续 第{len(messages)+1}部分)*\n\n"
            current_count = 0

        current_text += entry
        current_count += 1

    if current_count > 0:
        messages.append(_mk_payload(current_text, now_str, section_title))

    return messages


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
) -> None:
    """Send message(s) to DingTalk via webhook."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        for payload in messages:
            timestamp, sign = _sign(secret)
            url = f"{webhook_url}&timestamp={timestamp}&sign={sign}"
            try:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") != 0:
                    logger.error("DingTalk API error: %s", data)
                else:
                    logger.info("DingTalk message sent successfully")
            except Exception:
                logger.exception("Failed to send DingTalk message")


async def send_test_message(webhook_url: str, secret: str) -> None:
    """Send a simple test message to verify webhook config."""
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "🔧 RSS Bot 测试",
            "text": "### 🔧 RSS News Bot 测试消息\n\n"
            "如果你看到这条消息，说明钉钉 Webhook 配置正确！✅",
        },
    }
    await send_to_dingtalk(webhook_url, secret, [payload])
