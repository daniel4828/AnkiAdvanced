"""Tests for starring story sentences (issue #692).

Starred sentences are the positive examples Daniel collects while reviewing, to
feed back into prompt tuning. What matters beyond "the flag round-trips" is that
a starred sentence still knows *which prompt made it* — mode/model/episode_id
from the story's gen_params — because that context is the entire point.
"""

import sqlite3

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
import database
import database.core
import main

client = TestClient(main.app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    # database.core.DB_PATH, not database.DB_PATH — see conftest.py (#615).
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return tmp_path / "test.db"


def _make_story(deck_id=None, *, mode="knowledge", lang="zh", category="reading"):
    """Create a one-sentence story and return (story_id, sentence_id)."""
    if deck_id is None:
        deck_id = database.get_or_create_deck("StarDeck")
    story_id = database.create_story(
        "2026-08-11", category, deck_id,
        [{
            "position": 0,
            "sentence_zh": "他把复杂的想法说得很清楚。",
            "sentence_en": "He explained the complex idea clearly.",
            "sentence_de": "Er erklärte die komplexe Idee klar.",
            "source_title": "某播客单集",
            "source_url": "https://example.com/ep1",
        }],
        gen_params={"mode": mode, "model": "deepseek-chat", "episode_id": 7},
        lang=lang,
    )
    sentences = database.get_story_sentences(story_id)
    return story_id, sentences[0]["id"]


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

def test_migration_adds_columns_to_legacy_db(tmp_path, monkeypatch):
    """init_db() on a DB whose story_sentences predates #692 adds both columns,
    and running it a second time is a no-op (not an error)."""
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_file)
    conn.execute("""CREATE TABLE story_sentences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id INTEGER NOT NULL,
        position INTEGER NOT NULL,
        sentence_zh TEXT NOT NULL,
        sentence_en TEXT NOT NULL DEFAULT ''
    )""")
    conn.commit()
    conn.close()

    monkeypatch.setattr(database.core, "DB_PATH", str(db_file))
    database.init_db()
    database.init_db()  # idempotent

    conn = sqlite3.connect(db_file)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(story_sentences)")}
    conn.close()
    assert {"starred", "starred_at"} <= cols


def test_existing_sentences_default_to_unstarred(tmp_db):
    _, sentence_id = _make_story()
    assert database.get_starred_sentences() == []


# ---------------------------------------------------------------------------
# database layer
# ---------------------------------------------------------------------------

def test_star_and_unstar_round_trip(tmp_db):
    _, sentence_id = _make_story()

    result = database.set_sentence_starred(sentence_id, True)
    assert result["starred"] == 1
    assert result["starred_at"]

    starred = database.get_starred_sentences()
    assert [s["id"] for s in starred] == [sentence_id]

    result = database.set_sentence_starred(sentence_id, False)
    assert result["starred"] == 0
    assert result["starred_at"] is None
    assert database.get_starred_sentences() == []


def test_star_unknown_sentence_returns_none(tmp_db):
    assert database.set_sentence_starred(99999, True) is None


def test_starred_list_carries_generation_context(tmp_db):
    """Without mode/source, a starred sentence can't tell you which prompt to fix."""
    _, sentence_id = _make_story(mode="knowledge")
    database.set_sentence_starred(sentence_id, True)

    s = database.get_starred_sentences()[0]
    assert s["mode"] == "knowledge"
    assert s["model"] == "deepseek-chat"
    assert s["episode_id"] == 7
    assert s["story_date"] == "2026-08-11"
    assert s["deck_name"] == "StarDeck"
    assert s["source_title"] == "某播客单集"
    assert s["sentence_de"] == "Er erklärte die komplexe Idee klar."


def test_starred_list_newest_first(tmp_db):
    deck_id = database.get_or_create_deck("StarDeck")
    _, first = _make_story(deck_id)
    _, second = _make_story(deck_id, category="listening")

    database.set_sentence_starred(first, True)
    database.set_sentence_starred(second, True)
    # Same-second timestamps are broken by id DESC, so the later star still wins.
    assert [s["id"] for s in database.get_starred_sentences()][0] == second


def test_starred_list_filters_by_lang(tmp_db):
    deck_id = database.get_or_create_deck("StarDeck")
    _, zh = _make_story(deck_id, lang="zh")
    _, fr = _make_story(deck_id, lang="fr", category="listening")
    database.set_sentence_starred(zh, True)
    database.set_sentence_starred(fr, True)

    assert [s["id"] for s in database.get_starred_sentences(lang="fr")] == [fr]
    assert [s["id"] for s in database.get_starred_sentences(lang="zh")] == [zh]
    assert len(database.get_starred_sentences()) == 2


def test_again_regenerated_sentence_can_be_starred(tmp_db):
    """Again-regen sentences live under the 'again' sentinel category but are
    ordinary story_sentences rows — starring must work there too, since that's
    exactly where a freshly generated sentence gets judged."""
    deck_id = database.get_or_create_deck("StarDeck")
    # entries has no insert helper (only the importer writes it) — same approach
    # as tests/test_offline_sync.py.
    conn = database.get_db()
    word_id = conn.execute(
        "INSERT INTO entries (word_zh, definition) VALUES ('清楚', 'clear')"
    ).lastrowid
    conn.commit()
    conn.close()
    database.store_again_sentence(
        deck_id, word_id,
        {"sentence_zh": "说得很清楚。", "sentence_en": "Said clearly."},
        "2026-08-11",
    )
    again = database.get_again_sentence_for_word(word_id, "2026-08-11")

    assert database.set_sentence_starred(again["id"], True)["starred"] == 1
    assert [s["id"] for s in database.get_starred_sentences()] == [again["id"]]


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def test_api_star_round_trip(tmp_db):
    _, sentence_id = _make_story()

    r = client.post(f"/api/story-sentence/{sentence_id}/star", json={"starred": True})
    assert r.status_code == 200
    assert r.json()["starred"] == 1

    r = client.get("/api/starred-sentences")
    assert r.status_code == 200
    body = r.json()["sentences"]
    assert [s["id"] for s in body] == [sentence_id]
    assert body[0]["mode"] == "knowledge"

    r = client.post(f"/api/story-sentence/{sentence_id}/star", json={"starred": False})
    assert r.json()["starred"] == 0
    assert client.get("/api/starred-sentences").json()["sentences"] == []


def test_api_star_defaults_to_starring(tmp_db):
    _, sentence_id = _make_story()
    r = client.post(f"/api/story-sentence/{sentence_id}/star", json={})
    assert r.json()["starred"] == 1


def test_api_star_unknown_sentence_404(tmp_db):
    r = client.post("/api/story-sentence/99999/star", json={"starred": True})
    assert r.status_code == 404


def test_api_starred_sentences_lang_filter(tmp_db):
    deck_id = database.get_or_create_deck("StarDeck")
    _, zh = _make_story(deck_id, lang="zh")
    _, fr = _make_story(deck_id, lang="fr", category="listening")
    client.post(f"/api/story-sentence/{zh}/star", json={"starred": True})
    client.post(f"/api/story-sentence/{fr}/star", json={"starred": True})

    body = client.get("/api/starred-sentences?lang=fr").json()["sentences"]
    assert [s["id"] for s in body] == [fr]
