"""Tests for knowledge/ingest.py's ingest_text() (issue #668) and its HTTP
wrapper POST /api/knowledge/add-text (routes/knowledge.py).

Uses a real (throwaway, per-test) sqlite db via database.init_db() rather
than mocking database.* — ingest_text()/_store_article() touch several
database.podcast functions (get_episode_by_video_id, create_pending_episode,
update_episode) and the dedup behaviour is the whole point of these tests,
so exercising the real row-creation code is more honest than re-deriving
its contract in a stub. ai.translate_title is monkeypatched to avoid any
real AI call (CLAUDE.md: AI must be stubbed at ai._call_api / the public
function, tests never call out to a real provider).
"""
import re

import pytest

import ai
import database
import knowledge.ingest as ingest


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database.core, "DB_PATH", str(db_file))
    database.init_db()
    return db_file


@pytest.fixture(autouse=True)
def _no_title_translation(monkeypatch):
    """translate_title is a real (best-effort) AI call in production —
    stub it so these tests never reach a network/AI provider."""
    monkeypatch.setattr(ai, "translate_title", lambda title: None)


LONG_ARTICLE = "这是一篇粘贴进来的付费墙文章正文，用来测试知识库粘贴入库功能。" * 8
assert len(LONG_ARTICLE) >= 200


# ---------------------------------------------------------------------------
# ingest_text()
# ---------------------------------------------------------------------------

def test_ingest_text_creates_article_row():
    result = ingest.ingest_text("测试标题", LONG_ARTICLE)
    assert "episode_id" in result

    episode = database.get_episode(result["episode_id"])
    assert episode["kind"] == "article"
    assert episode["transcript_source"] == "pasted"
    assert episode["transcript_zh"] == LONG_ARTICLE
    assert episode["title"] == "测试标题"
    assert episode["video_id"].startswith("pasted:")


def test_ingest_text_stores_source_url_when_given():
    result = ingest.ingest_text("标题", LONG_ARTICLE, source_url="https://example.com/paywalled")
    episode = database.get_episode(result["episode_id"])
    assert episode["youtube_url"] == "https://example.com/paywalled"


def test_ingest_text_source_url_optional():
    result = ingest.ingest_text("标题", LONG_ARTICLE)
    episode = database.get_episode(result["episode_id"])
    # youtube_url is NOT NULL in schema.sql; no source_url given -> "".
    assert not episode["youtube_url"]


def test_ingest_text_duplicate_body_deduped():
    first = ingest.ingest_text("标题一", LONG_ARTICLE)
    second = ingest.ingest_text("标题二（同一篇正文再投一次）", LONG_ARTICLE)
    assert second == {"status": "already_exists", "episode_id": first["episode_id"]}

    # Only one row was actually created.
    episodes = database.get_db().execute(
        "SELECT COUNT(*) AS c FROM podcast_episodes WHERE kind='article'"
    ).fetchone()
    assert episodes["c"] == 1


def test_ingest_text_whitespace_differences_still_dedupe():
    """#668 completion criterion: the same article pasted with different
    line-wrapping/blank lines must not create a second row — the hash is
    computed over whitespace-normalized text. Uses paragraphs that already
    have a single-space/newline boundary between them, so collapsing
    whitespace-runs-to-one-space leaves the actual words untouched (an
    earlier version of this test inserted newlines *inside* words, which
    changes the normalized content and was a bug in the test, not the code)."""
    paragraphs = [LONG_ARTICLE[i:i + 40] for i in range(0, len(LONG_ARTICLE), 40)]
    variant_a = " ".join(paragraphs)          # single spaces
    variant_b = "\n\n  \n".join(paragraphs)   # blank lines + stray indentation
    # Sanity: the two variants are literally different strings, but carry
    # the same content once whitespace runs are collapsed to one space.
    assert variant_a != variant_b
    assert re.sub(r"\s+", " ", variant_a) == re.sub(r"\s+", " ", variant_b)

    first = ingest.ingest_text("标题", variant_a)
    second = ingest.ingest_text("标题（换行方式不同）", variant_b)
    assert second == {"status": "already_exists", "episode_id": first["episode_id"]}


def test_ingest_text_too_short_raises():
    with pytest.raises(ingest.IngestError):
        ingest.ingest_text("标题", "太短了")


def test_ingest_text_exactly_at_threshold_succeeds():
    text = "字" * ingest._MIN_TEXT_CHARS
    result = ingest.ingest_text("标题", text)
    assert "episode_id" in result


def test_ingest_text_one_under_threshold_raises():
    text = "字" * (ingest._MIN_TEXT_CHARS - 1)
    with pytest.raises(ingest.IngestError):
        ingest.ingest_text("标题", text)


def test_ingest_text_truncates_long_body():
    text = "字" * 20000
    result = ingest.ingest_text("标题", text)
    episode = database.get_episode(result["episode_id"])
    assert len(episode["transcript_zh"]) == ingest._MAX_TEXT_CHARS


def test_ingest_text_untitled_falls_back():
    result = ingest.ingest_text("", LONG_ARTICLE)
    episode = database.get_episode(result["episode_id"])
    assert episode["title"] == "(untitled)"


def test_ingest_text_reuses_store_article_not_a_second_pipeline():
    """Structural guard for the #668 requirement that ingest_text() must
    not duplicate _ingest_article's row-building code: both should funnel
    through the same _store_article helper."""
    import inspect
    src = inspect.getsource(ingest.ingest_text)
    assert "_store_article(" in src


# ---------------------------------------------------------------------------
# POST /api/knowledge/add-text — same response contract as /api/knowledge/add
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    pytest.importorskip("fastapi", reason="fastapi not installed")
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def test_add_text_endpoint_returns_episode_id(client):
    resp = client.post("/api/knowledge/add-text", json={"title": "标题", "text": LONG_ARTICLE})
    assert resp.status_code == 200
    body = resp.json()
    assert "episode_id" in body


def test_add_text_endpoint_dedup_returns_already_exists(client):
    first = client.post("/api/knowledge/add-text", json={"title": "标题", "text": LONG_ARTICLE})
    second = client.post("/api/knowledge/add-text", json={"title": "标题2", "text": LONG_ARTICLE})
    assert second.status_code == 200
    body = second.json()
    assert body == {"status": "already_exists", "episode_id": first.json()["episode_id"]}


def test_add_text_endpoint_too_short_returns_400(client):
    resp = client.post("/api/knowledge/add-text", json={"title": "标题", "text": "太短"})
    assert resp.status_code == 400


def test_add_text_endpoint_accepts_optional_source_url(client):
    resp = client.post(
        "/api/knowledge/add-text",
        json={"title": "标题", "text": LONG_ARTICLE, "source_url": "https://example.com/x"},
    )
    assert resp.status_code == 200
    episode = database.get_episode(resp.json()["episode_id"])
    assert episode["youtube_url"] == "https://example.com/x"
