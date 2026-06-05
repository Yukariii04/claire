"""
claire MCP Tools — Web
Tools: get_world_news, get_world_finance_news, open_world_monitor,
       open_finance_world_monitor, search_web, fetch_url
"""

import asyncio
import re
import webbrowser
import xml.etree.ElementTree as ET

import httpx
from duckduckgo_search import DDGS

# ── RSS Feed URLs ──────────────────────────────────────────────────────────

WORLD_NEWS_FEEDS = [
    ("BBC",       "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("CNBC",      "https://www.cnbc.com/id/100727362/device/rss/rss.html"),
    ("NYT",       "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("AlJazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
]

FINANCE_NEWS_FEEDS = [
    ("CNBC Finance", "https://www.cnbc.com/id/10000664/device/rss/rss.html"),
    ("Bloomberg",    "https://feeds.bloomberg.com/markets/news.rss"),
    ("Reuters",      "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best"),
    ("MarketWatch",  "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ("NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
]

HEADERS = {"User-Agent": "Claire-AI/1.0"}


# ── Helpers ────────────────────────────────────────────────────────────────

def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


async def _fetch_feed(client: httpx.AsyncClient, source: str, url: str) -> list[dict]:
    """Fetch a single RSS feed and return up to 5 items."""
    try:
        resp = await client.get(url, timeout=5, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        items = []
        for item in root.iter("item"):
            title = _strip_html(getattr(item.find("title"), "text", "") or "")
            desc  = _strip_html(getattr(item.find("description"), "text", "") or "")
            link  = (getattr(item.find("link"), "text", "") or "").strip()
            if title:
                items.append({
                    "source": source,
                    "title":  title,
                    "desc":   desc[:200],
                    "link":   link,
                })
            if len(items) >= 5:
                break
        return items
    except Exception:
        return []


def _format_items(items: list[dict], limit: int = 12) -> str:
    lines = []
    for it in items[:limit]:
        lines.append(f"**[{it['source']}]** {it['title']}")
        if it["desc"]:
            lines.append(f"{it['desc']}...")
        if it["link"]:
            lines.append(f"Link: {it['link']}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Tool Registration ──────────────────────────────────────────────────────

def register(mcp):

    @mcp.tool()
    async def get_world_news() -> str:
        """Fetch the latest world news headlines from BBC, CNBC, NYT, and Al Jazeera."""
        try:
            async with httpx.AsyncClient() as client:
                results = await asyncio.gather(
                    *[_fetch_feed(client, src, url) for src, url in WORLD_NEWS_FEEDS]
                )
            items = [item for feed in results for item in feed]
            if not items:
                return "The global news grid is unresponsive, sir."
            return _format_items(items, limit=12)
        except Exception:
            return "The global news grid is unresponsive, sir."

    @mcp.tool()
    async def get_world_finance_news() -> str:
        """Fetch the latest finance and market news headlines."""
        try:
            async with httpx.AsyncClient() as client:
                results = await asyncio.gather(
                    *[_fetch_feed(client, src, url) for src, url in FINANCE_NEWS_FEEDS]
                )
            items = [item for feed in results for item in feed]
            if not items:
                return "The financial feeds are unresponsive right now, sir."
            return _format_items(items, limit=12)
        except Exception:
            return "The financial feeds are unresponsive right now, sir."

    @mcp.tool()
    async def open_world_monitor() -> str:
        """Open the World Monitor dashboard in the default browser."""
        try:
            webbrowser.open("https://worldmonitor.app/")
            return "Displaying the World Monitor on your primary screen now, sir."
        except Exception as e:
            return f"I'm unable to initialize the visual monitor: {e}"

    @mcp.tool()
    async def open_finance_world_monitor() -> str:
        """Open the Finance World Monitor dashboard in the default browser."""
        try:
            webbrowser.open("https://finance.worldmonitor.app/")
            return "Displaying the Finance World Monitor on your primary screen now, sir."
        except Exception as e:
            return f"I'm unable to initialize the finance monitor: {e}"

    @mcp.tool()
    async def search_web(query: str) -> str:
        """Search the web using DuckDuckGo and return the top 5 results."""
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=5))
            if not results:
                return "Search is offline right now, boss."
            lines = []
            for r in results:
                lines.append(f"**{r.get('title', 'No title')}**")
                lines.append(r.get("body", ""))
                lines.append(f"Link: {r.get('href', '')}")
                lines.append("")
            return "\n".join(lines).strip()
        except Exception:
            return "Search is offline right now, boss."

    @mcp.tool()
    async def fetch_url(url: str) -> str:
        """Fetch the raw text content of any URL (max 4000 chars)."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(url, timeout=10, headers=HEADERS, follow_redirects=True)
                resp.raise_for_status()
                text = re.sub(r"<[^>]+>", "", resp.text)
                return text[:4000]
        except Exception as e:
            return f"Couldn't fetch that URL, boss. Error: {e}"
