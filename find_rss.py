"""
RSS Feed Discovery Script

Reads 智库及高校名单.xlsx, probes common RSS URL patterns for each organization,
verifies which ones return valid feeds, and writes the results to rss_feeds_found.json.

Usage:
    uv run find_rss.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

import feedparser
import httpx
import openpyxl

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

EXCEL_FILE = "智库及高校名单.xlsx"
OUTPUT_FILE = "rss_feeds_found.json"

_TIMEOUT = httpx.Timeout(25.0, connect=10.0)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Common RSS path suffixes to try for each domain
RSS_SUFFIXES = [
    "/feed/",
    "/feed",
    "/rss/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/rss/all.xml",
    "/feeds/posts/default",
    "/index.xml",
    "/atom.xml",
    "/news/rss",
    "/news/feed",
    "/news/rss.xml",
    "/publications/rss",
    "/publications/feed",
    "/publications/feed/",
    "/latest/rss",
    "/blog/feed",
    "/blog/rss",
    "/blog/rss.xml",
    "/content/feed",
    "/wp-json/wp/v2/posts?_embed&format=feed&type=rss2",
]

# Hard-coded known RSS URLs per domain (takes precedence over probing)
KNOWN_FEEDS: dict[str, list[dict]] = {
    "brookings.edu": [
        {"name": "Brookings Institution", "url": "https://www.brookings.edu/feed/", "category": "顶级智库"},
        {"name": "Brookings Institution", "url": "https://www.brookings.edu/rss.xml", "category": "顶级智库"},
    ],
    "cfr.org": [
        {"name": "Council on Foreign Relations (CFR)", "url": "https://www.cfr.org/feed/", "category": "顶级智库"},
        {"name": "Council on Foreign Relations (CFR)", "url": "https://www.cfr.org/rss/rss.xml", "category": "顶级智库"},
        {"name": "Council on Foreign Relations (CFR)", "url": "https://www.cfr.org/rss/publications.xml", "category": "顶级智库"},
    ],
    "carnegieendowment.org": [
        {"name": "Carnegie Endowment", "url": "https://carnegieendowment.org/rss/subscriptions/all-topics", "category": "顶级智库"},
        {"name": "Carnegie Endowment", "url": "https://carnegieendowment.org/rss/", "category": "顶级智库"},
        {"name": "Carnegie Endowment", "url": "https://carnegieendowment.org/feed/", "category": "顶级智库"},
    ],
    "csis.org": [
        {"name": "CSIS", "url": "https://www.csis.org/rss.xml", "category": "顶级智库"},
        {"name": "CSIS", "url": "https://www.csis.org/feed/", "category": "顶级智库"},
    ],
    "rand.org": [
        {"name": "RAND Corporation", "url": "https://www.rand.org/content/rand/pubs/feeds/rss.xml", "category": "顶级智库"},
        {"name": "RAND Corporation", "url": "https://www.rand.org/rss/", "category": "顶级智库"},
    ],
    "atlanticcouncil.org": [
        {"name": "Atlantic Council", "url": "https://www.atlanticcouncil.org/feed/", "category": "顶级智库"},
        {"name": "Atlantic Council", "url": "https://www.atlanticcouncil.org/rss.xml", "category": "顶级智库"},
    ],
    "aei.org": [
        {"name": "American Enterprise Institute (AEI)", "url": "https://www.aei.org/feed/", "category": "顶级智库"},
        {"name": "American Enterprise Institute (AEI)", "url": "https://www.aei.org/rss.xml", "category": "顶级智库"},
    ],
    "heritage.org": [
        {"name": "Heritage Foundation", "url": "https://www.heritage.org/feeds/all.rss", "category": "顶级智库"},
        {"name": "Heritage Foundation", "url": "https://www.heritage.org/rss/recent-research.xml", "category": "顶级智库"},
        {"name": "Heritage Foundation", "url": "https://www.heritage.org/feed/", "category": "顶级智库"},
    ],
    "hudson.org": [
        {"name": "Hudson Institute", "url": "https://www.hudson.org/feed", "category": "顶级智库"},
        {"name": "Hudson Institute", "url": "https://www.hudson.org/rss.xml", "category": "顶级智库"},
        {"name": "Hudson Institute", "url": "https://www.hudson.org/rss/publications", "category": "顶级智库"},
    ],
    "fpri.org": [
        {"name": "FPRI", "url": "https://www.fpri.org/feed/", "category": "顶级智库"},
        {"name": "FPRI", "url": "https://www.fpri.org/rss.xml", "category": "顶级智库"},
    ],
    "americanprogress.org": [
        {"name": "Center for American Progress", "url": "https://www.americanprogress.org/feed/", "category": "顶级智库"},
    ],
    "cato.org": [
        {"name": "Cato Institute", "url": "https://feeds.cato.org/CatoRecentOpeds", "category": "顶级智库"},
        {"name": "Cato Institute", "url": "https://www.cato.org/rss/recent_op-eds.xml", "category": "顶级智库"},
        {"name": "Cato Institute", "url": "https://www.cato.org/rss/daily-commentary.xml", "category": "顶级智库"},
        {"name": "Cato Institute", "url": "https://www.cato.org/rss/recent-publications.xml", "category": "顶级智库"},
    ],
    "hoover.org": [
        {"name": "Hoover Institution", "url": "https://www.hoover.org/rss/publications", "category": "顶级智库"},
        {"name": "Hoover Institution", "url": "https://www.hoover.org/rss/", "category": "顶级智库"},
        {"name": "Hoover Institution", "url": "https://www.hoover.org/feed/", "category": "顶级智库"},
    ],
    "wilsoncenter.org": [
        {"name": "Wilson Center", "url": "https://www.wilsoncenter.org/rss.xml", "category": "顶级智库"},
        {"name": "Wilson Center", "url": "https://www.wilsoncenter.org/feed/", "category": "顶级智库"},
    ],
    "chathamhouse.org": [
        {"name": "Chatham House", "url": "https://www.chathamhouse.org/rss.xml", "category": "顶级智库"},
        {"name": "Chatham House", "url": "https://www.chathamhouse.org/feed/", "category": "顶级智库"},
    ],
    "iiss.org": [
        {"name": "IISS", "url": "https://www.iiss.org/publications/rss", "category": "顶级智库"},
        {"name": "IISS", "url": "https://www.iiss.org/en/rss.xml", "category": "顶级智库"},
        {"name": "IISS", "url": "https://www.iiss.org/feed/", "category": "顶级智库"},
    ],
    "bruegel.org": [
        {"name": "Bruegel", "url": "https://www.bruegel.org/rss.xml", "category": "顶级智库"},
        {"name": "Bruegel", "url": "https://www.bruegel.org/feed/", "category": "顶级智库"},
    ],
    "ifri.org": [
        {"name": "IFRI", "url": "https://www.ifri.org/rss.xml", "category": "顶级智库"},
        {"name": "IFRI", "url": "https://www.ifri.org/en/rss.xml", "category": "顶级智库"},
        {"name": "IFRI", "url": "https://www.ifri.org/feed/", "category": "顶级智库"},
    ],
    "ecfr.eu": [
        {"name": "ECFR", "url": "https://ecfr.eu/feed/", "category": "顶级智库"},
        {"name": "ECFR", "url": "https://ecfr.eu/rss.xml", "category": "顶级智库"},
    ],
    "gmfus.org": [
        {"name": "German Marshall Fund", "url": "https://www.gmfus.org/rss.xml", "category": "顶级智库"},
        {"name": "German Marshall Fund", "url": "https://www.gmfus.org/feed/", "category": "顶级智库"},
    ],
    "clingendael.org": [
        {"name": "Clingendael Institute", "url": "https://www.clingendael.org/rss.xml", "category": "顶级智库"},
        {"name": "Clingendael Institute", "url": "https://www.clingendael.org/feed/", "category": "顶级智库"},
    ],
    "ceps.eu": [
        {"name": "CEPS", "url": "https://www.ceps.eu/feed/", "category": "顶级智库"},
        {"name": "CEPS", "url": "https://www.ceps.eu/rss.xml", "category": "顶级智库"},
    ],
    "epc.eu": [
        {"name": "European Policy Centre", "url": "https://www.epc.eu/feed/", "category": "顶级智库"},
        {"name": "European Policy Centre", "url": "https://www.epc.eu/rss.xml", "category": "顶级智库"},
    ],
    "carnegieeurope.eu": [
        {"name": "Carnegie Europe", "url": "https://carnegieeurope.eu/rss/", "category": "顶级智库"},
        {"name": "Carnegie Europe", "url": "https://carnegieeurope.eu/feed/", "category": "顶级智库"},
    ],
    "carnegie-mec.org": [
        {"name": "Carnegie Middle East", "url": "https://carnegie-mec.org/rss/", "category": "重要智库"},
        {"name": "Carnegie Middle East", "url": "https://carnegie-mec.org/feed/", "category": "重要智库"},
    ],
    "mei.edu": [
        {"name": "Middle East Institute", "url": "https://www.mei.edu/rss.xml", "category": "重要智库"},
        {"name": "Middle East Institute", "url": "https://www.mei.edu/feed/", "category": "重要智库"},
    ],
    "inss.org.il": [
        {"name": "INSS", "url": "https://www.inss.org.il/rss/", "category": "重要智库"},
        {"name": "INSS", "url": "https://www.inss.org.il/feed/", "category": "重要智库"},
    ],
    "besacenter.org": [
        {"name": "BESA Center", "url": "https://besacenter.org/feed/", "category": "重要智库"},
        {"name": "BESA Center", "url": "https://besacenter.org/feed", "category": "重要智库"},
    ],
    "timep.org": [
        {"name": "TIMEP", "url": "https://timep.org/feed/", "category": "重要智库"},
    ],
    "globaltaiwan.org": [
        {"name": "Global Taiwan Institute (GTI)", "url": "https://globaltaiwan.org/feed/", "category": "重点院校"},
    ],
    "studies.aljazeera.net": [
        {"name": "Al Jazeera Centre for Studies", "url": "https://studies.aljazeera.net/en/rss", "category": "重要智库"},
        {"name": "Al Jazeera Centre for Studies", "url": "https://studies.aljazeera.net/rss.xml", "category": "重要智库"},
    ],
    "epc.ae": [
        {"name": "Emirates Policy Center", "url": "https://www.epc.ae/feed/", "category": "重要智库"},
        {"name": "Emirates Policy Center", "url": "https://www.epc.ae/rss.xml", "category": "重要智库"},
    ],
    "lcps-lebanon.org": [
        {"name": "LCPS Lebanon", "url": "https://www.lcps-lebanon.org/feed/", "category": "重要智库"},
    ],
}


def _extract_domain(url: str) -> str:
    """Extract the main domain from a URL."""
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    return match.group(1) if match else url


def _load_orgs_from_excel(path: str) -> list[dict]:
    """Parse the Excel sheet and return a list of org dicts."""
    wb = openpyxl.load_workbook(path)
    ws = wb["Sheet1"]
    orgs = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:  # skip header
            continue
        region, org_type, name, website = (row[j] for j in range(4))
        if not name or not website:
            continue
        orgs.append({
            "region": region or "",
            "type": org_type or "",
            "name": str(name).strip(),
            "website": str(website).strip().rstrip("/"),
        })
    return orgs


async def _verify_rss(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if the URL looks like a valid RSS/Atom feed."""
    try:
        resp = await client.get(url, follow_redirects=True)
        if resp.status_code != 200:
            return False
        ct = resp.headers.get("content-type", "")
        # Accept XML-ish content types
        if any(k in ct for k in ("xml", "rss", "atom", "text/plain")):
            parsed = feedparser.parse(resp.text)
            return len(parsed.entries) > 0
        # Also try parsing even without correct content-type
        parsed = feedparser.parse(resp.text)
        return len(parsed.entries) > 0
    except Exception:
        return False


async def _probe_feeds(
    client: httpx.AsyncClient,
    org: dict,
) -> list[dict]:
    """Try known feeds first, then probe common RSS suffixes."""
    website = org["website"]
    domain = _extract_domain(website)

    # Check hard-coded known feeds
    for key, feeds in KNOWN_FEEDS.items():
        if domain.endswith(key) or key in domain:
            verified = []
            for f in feeds:
                ok = await _verify_rss(client, f["url"])
                status = "✓" if ok else "✗"
                logger.info("[%s] %s %s", status, org["name"], f["url"])
                if ok:
                    verified.append(f)
            if verified:
                return verified
            break  # known but not verified; fall through to probing

    # Probe common suffixes
    for suffix in RSS_SUFFIXES:
        url = f"{website}{suffix}"
        if await _verify_rss(client, url):
            logger.info("[✓] %s %s", org["name"], url)
            return [{"name": org["name"], "url": url, "category": org["type"]}]

    logger.warning("[✗] No RSS found for %s (%s)", org["name"], website)
    return []


async def discover_all_feeds(orgs: list[dict]) -> list[dict]:
    """Discover RSS feeds for all orgs concurrently."""
    async with httpx.AsyncClient(timeout=_TIMEOUT, headers=_HEADERS) as client:
        tasks = [_probe_feeds(client, org) for org in orgs]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    found: list[dict] = []
    for r in results:
        if isinstance(r, list):
            found.extend(r)
    return found


def main() -> None:
    orgs = _load_orgs_from_excel(EXCEL_FILE)
    logger.info("Loaded %d organizations from Excel", len(orgs))

    feeds = asyncio.run(discover_all_feeds(orgs))
    logger.info("Discovered %d working RSS feeds", len(feeds))

    # Deduplicate by URL
    seen_urls: set[str] = set()
    unique_feeds = []
    for f in feeds:
        if f["url"] not in seen_urls:
            seen_urls.add(f["url"])
            unique_feeds.append(f)

    output = Path(OUTPUT_FILE)
    output.write_text(json.dumps(unique_feeds, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Results written to %s", OUTPUT_FILE)

    print("\n=== Summary ===")
    for f in unique_feeds:
        print(f"  [{f['category']}] {f['name']}: {f['url']}")
    print(f"\nTotal: {len(unique_feeds)} feeds")


if __name__ == "__main__":
    main()
