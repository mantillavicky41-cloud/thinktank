"""Generic article extraction for non-RSS HTML sources."""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

_JSON_LD_RE = re.compile(
    r"<script[^>]+type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")
_YEAR_PATH_RE = re.compile(r"/20\d{2}/")

_CONTENT_HINTS = (
    "analysis",
    "article",
    "blog",
    "brief",
    "commentary",
    "conference",
    "event",
    "events",
    "feature",
    "insight",
    "issue",
    "news",
    "opinion",
    "policy",
    "post",
    "program",
    "publication",
    "report",
    "research",
    "seminar",
    "talk",
    "taiwan",
)
_NAV_HINTS = (
    "about",
    "account",
    "author",
    "calendar",
    "careers",
    "contact",
    "cookie",
    "donate",
    "facebook",
    "instagram",
    "jobs",
    "linkedin",
    "login",
    "newsletter",
    "people",
    "podcast",
    "privacy",
    "search",
    "staff",
    "subscribe",
    "team",
    "terms",
    "twitter",
    "youtube",
)
_SCHEMES_TO_SKIP = ("#", "javascript:", "mailto:", "tel:")
_JSON_LD_TYPES = {
    "article",
    "blogposting",
    "event",
    "newsarticle",
    "report",
    "scholarlyarticle",
}


def clean_text(raw: str) -> str:
    text = _TAG_RE.sub(" ", raw or "")
    text = unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_published_at(raw: str | None) -> str | None:
    if not raw:
        return None

    raw = str(raw).strip()
    if not raw:
        return None

    normalized = raw.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
        return dt.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        pass

    match = re.search(r"\d{4}-\d{2}-\d{2}", raw)
    if match:
        return f"{match.group(0)} 00:00"
    return None


def _same_site(left: str, right: str) -> bool:
    left = left.removeprefix("www.")
    right = right.removeprefix("www.")
    return left == right or left.endswith(f".{right}") or right.endswith(f".{left}")


def _iter_nodes(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_nodes(item)


def _extract_json_ld_articles(html: str, page_url: str) -> list[dict[str, str | None]]:
    page_domain = urlparse(page_url).netloc
    results: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for block in _JSON_LD_RE.findall(html):
        try:
            payload = json.loads(unescape(block.strip()))
        except json.JSONDecodeError:
            continue

        for node in _iter_nodes(payload):
            raw_type = node.get("@type")
            if isinstance(raw_type, list):
                types = {str(item).lower() for item in raw_type}
            else:
                types = {str(raw_type).lower()} if raw_type else set()
            if not types.intersection(_JSON_LD_TYPES):
                continue

            title = clean_text(str(node.get("headline") or node.get("name") or ""))
            raw_link = str(node.get("url") or node.get("@id") or "").strip()
            if not title or not raw_link:
                continue

            link = urljoin(page_url, raw_link)
            if not _same_site(urlparse(link).netloc, page_domain):
                continue
            if link in seen:
                continue
            seen.add(link)

            results.append(
                {
                    "title": title,
                    "summary": clean_text(str(node.get("description") or "")),
                    "link": link,
                    "published_at": normalize_published_at(
                        node.get("datePublished")
                        or node.get("dateCreated")
                        or node.get("startDate")
                    ),
                }
            )

    return results


class _AnchorCollector(HTMLParser):
    """Collect anchors with visible text."""

    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_title: str = ""
        self._current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag != "a":
            return
        attr_map = {str(k).lower(): str(v) for k, v in attrs}
        self._current_href = attr_map.get("href", "").strip()
        self._current_title = attr_map.get("title", "").strip()
        self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag != "a" or not self._current_href:
            return

        text = clean_text(" ".join(self._current_text))
        if self._current_title and len(text) < len(self._current_title):
            text = self._current_title

        self.anchors.append({"href": self._current_href, "text": text})
        self._current_href = None
        self._current_title = ""
        self._current_text = []


def _score_anchor(href: str, text: str, page_url: str) -> tuple[int, str] | None:
    href = (href or "").strip()
    if not href or href.startswith(_SCHEMES_TO_SKIP):
        return None

    link = urljoin(page_url, href)
    parsed_link = urlparse(link)
    parsed_page = urlparse(page_url)

    if parsed_link.scheme not in ("http", "https"):
        return None
    if not _same_site(parsed_link.netloc, parsed_page.netloc):
        return None
    if parsed_link.path == parsed_page.path and not parsed_link.query:
        return None

    title = clean_text(text)
    if len(title) < 12:
        return None

    haystack = f"{parsed_link.path.lower()} {title.lower()}"
    score = 0

    if len(title.split()) >= 4:
        score += 2
    if 18 <= len(title) <= 180:
        score += 2
    if any(hint in haystack for hint in _CONTENT_HINTS):
        score += 4
    if any(hint in haystack for hint in _NAV_HINTS):
        score -= 6
    if _YEAR_PATH_RE.search(parsed_link.path):
        score += 3
    if parsed_link.path.endswith(".pdf"):
        score += 2
    if len(parsed_link.path.strip("/").split("/")) >= 2:
        score += 1

    if score < 3:
        return None
    return score, link


def _extract_anchor_articles(html: str, page_url: str) -> list[dict[str, str | None]]:
    parser = _AnchorCollector()
    parser.feed(html)

    scored: list[tuple[int, int, str, str]] = []
    for index, anchor in enumerate(parser.anchors):
        score = _score_anchor(anchor["href"], anchor["text"], page_url)
        if not score:
            continue
        points, link = score
        scored.append((points, index, link, anchor["text"]))

    scored.sort(key=lambda item: (-item[0], item[1]))

    results: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for _, _, link, title in scored:
        if link in seen:
            continue
        seen.add(link)
        results.append(
            {
                "title": clean_text(title),
                "summary": "",
                "link": link,
                "published_at": None,
            }
        )
        if len(results) >= 25:
            break
    return results


def extract_html_articles(html: str, page_url: str) -> list[dict[str, str | None]]:
    """Extract monitorable article/event items from a non-RSS page."""
    results: list[dict[str, str | None]] = []
    seen: set[str] = set()

    for item in _extract_json_ld_articles(html, page_url):
        link = str(item["link"])
        if link in seen:
            continue
        seen.add(link)
        results.append(item)

    for item in _extract_anchor_articles(html, page_url):
        link = str(item["link"])
        if link in seen:
            continue
        seen.add(link)
        results.append(item)

    return results[:25]
