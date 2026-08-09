"""Tests for knowledge/article.py (issue #652).

normalize_url is pure (no I/O). fetch_article makes real network calls via
trafilatura in production, so every test here stubs trafilatura's module
functions — CLAUDE.md is explicit that this suite must never actually reach
the network.
"""
import pytest

import knowledge.article as ka


# ---------------------------------------------------------------------------
# normalize_url
# ---------------------------------------------------------------------------

def test_normalize_url_strips_utm_params():
    url = "https://example.com/news/article-1?utm_source=twitter&utm_medium=social&utm_campaign=x"
    assert ka.normalize_url(url) == "https://example.com/news/article-1"


def test_normalize_url_strips_fbclid_and_gclid():
    assert ka.normalize_url(
        "https://example.com/a?fbclid=abc123"
    ) == "https://example.com/a"
    assert ka.normalize_url(
        "https://example.com/a?gclid=xyz789"
    ) == "https://example.com/a"


def test_normalize_url_strips_fragment():
    assert ka.normalize_url("https://example.com/a#comments") == "https://example.com/a"


def test_normalize_url_keeps_real_query_params():
    """A tracking param sitting next to a real one (e.g. a paginated/query
    article) must not lose the real param."""
    url = "https://example.com/search?q=news&utm_source=app"
    assert ka.normalize_url(url) == "https://example.com/search?q=news"


def test_normalize_url_different_share_links_land_on_same_key():
    """The same article shared via Twitter vs. a newsletter vs. WeChat must
    normalize to the identical canonical URL — this is podcast_episodes'
    dedup key (video_id, UNIQUE), so a mismatch here means duplicate rows."""
    twitter_link = "https://example.com/news/big-story?utm_source=twitter&utm_medium=social"
    newsletter_link = "https://example.com/news/big-story?utm_source=newsletter&utm_campaign=weekly"
    wechat_link = "https://example.com/news/big-story?from=wechat&spm=abc.123"
    assert ka.normalize_url(twitter_link) == ka.normalize_url(newsletter_link) == ka.normalize_url(wechat_link)
    assert ka.normalize_url(twitter_link) == "https://example.com/news/big-story"


def test_normalize_url_idempotent():
    already_clean = "https://example.com/news/story"
    assert ka.normalize_url(already_clean) == already_clean
    assert ka.normalize_url(ka.normalize_url(already_clean)) == ka.normalize_url(already_clean)


def test_normalize_url_strips_leading_trailing_whitespace():
    assert ka.normalize_url("  https://example.com/a?utm_source=x  ") == "https://example.com/a"


# ---------------------------------------------------------------------------
# fetch_article — trafilatura stubbed
# ---------------------------------------------------------------------------

class _FakeMetadata:
    def __init__(self, title=None, date=None):
        self.title = title
        self.date = date


def _patch_trafilatura(monkeypatch, *, downloaded="<html>ok</html>", extracted="", metadata=None,
                        fetch_raises=None):
    import trafilatura

    def fake_fetch_url(url, *a, **kw):
        if fetch_raises:
            raise fetch_raises
        return downloaded

    monkeypatch.setattr(trafilatura, "fetch_url", fake_fetch_url)
    monkeypatch.setattr(trafilatura, "extract", lambda *a, **kw: extracted)
    monkeypatch.setattr(trafilatura, "extract_metadata", lambda *a, **kw: metadata)


def test_fetch_article_download_failure_raises(monkeypatch):
    _patch_trafilatura(monkeypatch, downloaded=None)
    with pytest.raises(ka.ArticleExtractionError):
        ka.fetch_article("https://example.com/paywalled")


def test_fetch_article_body_too_short_raises(monkeypatch):
    """A paywall/login-wall page typically yields only a short nav-bar
    fragment — anything under 200 chars must be treated as a failure, never
    silently stored as if it were the article (see issue #652)."""
    _patch_trafilatura(monkeypatch, extracted="订阅解锁全文" * 5)  # well under 200 chars
    with pytest.raises(ka.ArticleExtractionError):
        ka.fetch_article("https://example.com/paywalled")


def test_fetch_article_success_returns_title_text_site_date(monkeypatch):
    body = "这是一篇完整的新闻文章。" * 30  # comfortably over 200 chars
    _patch_trafilatura(
        monkeypatch,
        extracted=body,
        metadata=_FakeMetadata(title="新闻标题", date="2026-08-09"),
    )
    result = ka.fetch_article("https://www.example.com/news/story")
    assert result["title"] == "新闻标题"
    assert result["text"] == body
    assert result["site"] == "example.com"  # www. stripped
    assert result["published_at"] == "2026-08-09"


def test_fetch_article_falls_back_to_url_when_no_title(monkeypatch):
    body = "x" * 300
    _patch_trafilatura(monkeypatch, extracted=body, metadata=None)
    result = ka.fetch_article("https://example.com/untitled")
    assert result["title"] == "https://example.com/untitled"
    assert result["published_at"] is None


def test_fetch_article_truncates_long_body(monkeypatch):
    body = "字" * 20000
    _patch_trafilatura(monkeypatch, extracted=body, metadata=_FakeMetadata(title="长文"))
    result = ka.fetch_article("https://example.com/long")
    assert len(result["text"]) == ka._MAX_ARTICLE_CHARS


def test_fetch_article_propagates_download_exceptions(monkeypatch):
    _patch_trafilatura(monkeypatch, fetch_raises=OSError("network unreachable"))
    with pytest.raises(OSError):
        ka.fetch_article("https://example.com/whatever")


# ---------------------------------------------------------------------------
# fetch_transcript — podcast.fetch_transcript-compatible wrapper
# ---------------------------------------------------------------------------

def test_fetch_transcript_returns_text_and_meta(monkeypatch):
    body = "文章正文" * 60
    _patch_trafilatura(monkeypatch, extracted=body, metadata=_FakeMetadata(title="标题"))
    text, meta = ka.fetch_transcript({"youtube_url": "https://example.com/a", "video_id": "https://example.com/a"})
    assert text == body
    assert meta == {"transcript_source": "article"}


def test_fetch_transcript_uses_video_id_when_no_youtube_url(monkeypatch):
    seen = {}

    def fake_fetch_article(url):
        seen["url"] = url
        return {"title": "t", "text": "x" * 300, "site": "example.com", "published_at": None}

    monkeypatch.setattr(ka, "fetch_article", fake_fetch_article)
    ka.fetch_transcript({"video_id": "https://example.com/fallback"})
    assert seen["url"] == "https://example.com/fallback"


def test_fetch_transcript_propagates_extraction_error(monkeypatch):
    _patch_trafilatura(monkeypatch, extracted="too short")
    with pytest.raises(ka.ArticleExtractionError):
        ka.fetch_transcript({"youtube_url": "https://example.com/paywalled"})
