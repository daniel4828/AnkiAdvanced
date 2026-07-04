"""Fetch daily news from configured sources (Tagesschau API + RSS feeds).

Zero external dependencies: urllib, json, xml.etree, html, re only.

Sources are configured in data/news_sources.json (created with defaults on
first use). Results are cached per Anki day in data/news_cache/YYYY-MM-DD.json
so repeated story generations on the same day do not re-fetch.

Unified item shape: {"url": str, "title": str, "text": str, "source_name": str}
"""
from __future__ import annotations

import html
import json
import logging
import re
import urllib.request
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "data"
SOURCES_PATH = DATA_DIR / "news_sources.json"
CACHE_DIR = DATA_DIR / "news_cache"

# NOTE: the Tagesschau URL must NOT have a trailing slash — with a slash the
# API 301-redirects and some clients end up with an empty body.
DEFAULT_SOURCES = [
    {"name": "Tagesschau", "type": "tagesschau",
     "url": "https://www.tagesschau.de/api2u/homepage", "enabled": True},
    {"name": "BBC World", "type": "rss",
     "url": "http://feeds.bbci.co.uk/news/world/rss.xml", "enabled": True},
]

MAX_ITEMS_PER_SOURCE = 12
MAX_TEXT_CHARS = 600
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class NewsFetchError(Exception):
    """Raised when no news could be fetched from any enabled source."""


def _clean(raw: str | None) -> str:
    if not raw:
        return ""
    text = html.unescape(_TAG_RE.sub(" ", raw))
    return _WS_RE.sub(" ", text).strip()


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _fetch_tagesschau(source: dict) -> list[dict]:
    data = json.loads(_http_get(source["url"]))
    items = []
    for n in data.get("news", []):
        if n.get("type") != "story":
            continue  # skip videos / weather etc.
        title = _clean(n.get("title"))
        topline = _clean(n.get("topline"))
        if topline:
            title = f"{topline}: {title}"
        paragraphs = [_clean(c.get("value"))
                      for c in (n.get("content") or []) if c.get("type") == "text"]
        text = " ".join(p for p in [_clean(n.get("firstSentence")), *paragraphs] if p)
        items.append({
            "url": n.get("detailsweb") or n.get("shareURL") or "",
            "title": title,
            "text": text[:MAX_TEXT_CHARS],
            "source_name": source["name"],
        })
        if len(items) >= MAX_ITEMS_PER_SOURCE:
            break
    return items


def _fetch_rss(source: dict) -> list[dict]:
    root = ElementTree.fromstring(_http_get(source["url"]))
    items = []
    for it in root.findall(".//item")[:MAX_ITEMS_PER_SOURCE]:
        def _t(tag: str) -> str:
            el = it.find(tag)
            return _clean(el.text if el is not None else "")
        items.append({
            "url": _t("link"),
            "title": _t("title"),
            "text": _t("description")[:MAX_TEXT_CHARS],
            "source_name": source["name"],
        })
    return items


_FETCHERS = {"tagesschau": _fetch_tagesschau, "rss": _fetch_rss}


def load_sources() -> list[dict]:
    """Read the source list, writing the default config on first use."""
    if not SOURCES_PATH.exists():
        SOURCES_PATH.parent.mkdir(parents=True, exist_ok=True)
        SOURCES_PATH.write_text(
            json.dumps(DEFAULT_SOURCES, ensure_ascii=False, indent=2),
            encoding="utf-8")
        logger.info("news_fetcher: wrote default sources to %s", SOURCES_PATH)
    return json.loads(SOURCES_PATH.read_text(encoding="utf-8"))


def fetch_all(force: bool = False, today: str | None = None) -> list[dict]:
    """Return today's news items from all enabled sources (per-day cache).

    Raises NewsFetchError if every enabled source fails — callers must surface
    the error instead of silently falling back to another story mode.
    """
    today = today or date.today().isoformat()
    cache_file = CACHE_DIR / f"{today}.json"
    if not force and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    items: list[dict] = []
    errors: list[str] = []
    for source in load_sources():
        if not source.get("enabled", True):
            continue
        fetcher = _FETCHERS.get(source.get("type"))
        if fetcher is None:
            errors.append(f"{source.get('name')}: unknown type {source.get('type')!r}")
            continue
        try:
            fetched = fetcher(source)
            items.extend(fetched)
            logger.info("news_fetcher: %s -> %d items", source["name"], len(fetched))
        except Exception as exc:  # network / parse errors per source
            logger.warning("news_fetcher: %s failed: %s", source["name"], exc)
            errors.append(f"{source.get('name')}: {exc}")

    if not items:
        raise NewsFetchError("No news could be fetched: " + "; ".join(errors))

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(items, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return items


def cached_today_count(today: str | None = None) -> int | None:
    """Number of items in today's cache, or None if nothing was fetched yet."""
    today = today or date.today().isoformat()
    cache_file = CACHE_DIR / f"{today}.json"
    if not cache_file.exists():
        return None
    try:
        return len(json.loads(cache_file.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return None
