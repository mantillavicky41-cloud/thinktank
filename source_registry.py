"""Source registry built from the spreadsheet plus discovered RSS feeds."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

import openpyxl

EXCEL_FILE = Path("智库及高校名单.xlsx")
DISCOVERED_FEEDS_FILE = Path("rss_feeds_found.json")


def _normalize_url(url: str) -> str:
    return str(url).strip().rstrip("/")


def _domain_key(url: str) -> str:
    return urlparse(_normalize_url(url)).netloc.removeprefix("www.")


def _is_root_website(url: str) -> bool:
    path = urlparse(_normalize_url(url)).path.rstrip("/")
    return path == ""


def load_orgs_from_excel(path: Path = EXCEL_FILE) -> list[dict[str, str]]:
    """Load organizations from the spreadsheet."""
    wb = openpyxl.load_workbook(path)
    ws = wb["Sheet1"]

    orgs: list[dict[str, str]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue

        values = list(row) + [None] * 4
        region, org_type, name, website = values[:4]
        if not name or not website:
            continue

        orgs.append(
            {
                "region": str(region or "").strip(),
                "type": str(org_type or "综合").strip(),
                "name": str(name).strip(),
                "website": _normalize_url(str(website)),
            }
        )

    return orgs


def load_discovered_feeds(
    path: Path = DISCOVERED_FEEDS_FILE,
) -> dict[str, dict[str, str]]:
    """Load discovered RSS feeds keyed by exact spreadsheet name."""
    if not path.exists():
        return {}

    raw = json.loads(path.read_text(encoding="utf-8"))
    feeds: dict[str, dict[str, str]] = {}
    for item in raw:
        name = str(item.get("name", "")).strip()
        url = str(item.get("url", "")).strip()
        if not name or not url:
            continue

        feeds[name] = {
            "name": name,
            "url": url,
            "category": str(item.get("category", "综合")).strip() or "综合",
        }
    return feeds


def build_default_source_dicts() -> list[dict[str, str]]:
    """Build monitor sources for every spreadsheet row.

    If an RSS feed was discovered for the exact organization name, use it.
    Otherwise fall back to HTML page monitoring on the original website.
    """
    orgs = load_orgs_from_excel()
    discovered = load_discovered_feeds()
    discovered_by_domain = {
        _domain_key(feed["url"]): feed
        for feed in discovered.values()
    }

    sources: list[dict[str, str]] = []
    for org in orgs:
        feed = discovered.get(org["name"])
        if not feed and _is_root_website(org["website"]):
            feed = discovered_by_domain.get(_domain_key(org["website"]))
        if feed:
            sources.append(
                {
                    "name": org["name"],
                    "url": feed["url"],
                    "category": org["type"],
                    "kind": "rss",
                    "site_url": org["website"],
                }
            )
            continue

        sources.append(
            {
                "name": org["name"],
                "url": org["website"],
                "category": org["type"],
                "kind": "html",
                "site_url": org["website"],
            }
        )

    return sources
