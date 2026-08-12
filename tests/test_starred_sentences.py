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
    assert {"starred", "starred_at", "bad_reason"} <= cols


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


# ---------------------------------------------------------------------------
# Linking a starred sentence back to the prompt that made it (#697)
# ---------------------------------------------------------------------------

def test_starred_list_links_to_its_story_without_inlining_the_prompt(tmp_db):
    """A knowledge prompt embeds up to 15000 chars of transcript. Inlining it in a
    500-row list would make the response tens of MB — so the list carries the link
    (story_id) and a has_prompt flag, and the text is fetched on demand."""
    story_id, sentence_id = _make_story()
    conn = database.get_db()
    conn.execute("UPDATE stories SET prompt_text = ? WHERE id = ?",
                 ("完整的提示词正文……", story_id))
    conn.commit()
    conn.close()
    database.set_sentence_starred(sentence_id, True)

    s = database.get_starred_sentences()[0]
    assert s["story_id"] == story_id
    assert s["has_prompt"] == 1
    assert "prompt_text" not in s
    assert "完整的提示词正文" not in str(s)


def test_has_prompt_false_when_prompt_was_stripped(tmp_db):
    """The offline snapshot clears stories.prompt_text (offline_sync_server.py), and
    legacy stories predate the column — the UI has to be able to say so."""
    _, sentence_id = _make_story()
    database.set_sentence_starred(sentence_id, True)
    assert database.get_starred_sentences()[0]["has_prompt"] == 0


def test_get_story_prompt(tmp_db):
    story_id, _ = _make_story(mode="knowledge")
    conn = database.get_db()
    conn.execute("UPDATE stories SET prompt_text = ? WHERE id = ?", ("提示词正文", story_id))
    conn.commit()
    conn.close()

    p = database.get_story_prompt(story_id)
    assert p["prompt"] == "提示词正文"
    assert p["mode"] == "knowledge"
    assert p["model"] == "deepseek-chat"
    assert p["date"] == "2026-08-11"


def test_get_story_prompt_missing_story_is_none(tmp_db):
    assert database.get_story_prompt(99999) is None


def test_get_story_prompt_empty_when_stripped(tmp_db):
    """No prompt is a normal state, not an error — distinct from "no such story"."""
    story_id, _ = _make_story()
    assert database.get_story_prompt(story_id)["prompt"] == ""


def test_api_story_prompt_round_trip(tmp_db):
    story_id, _ = _make_story()
    conn = database.get_db()
    conn.execute("UPDATE stories SET prompt_text = ? WHERE id = ?", ("提示词正文", story_id))
    conn.commit()
    conn.close()

    r = client.get(f"/api/story-prompt/{story_id}")
    assert r.status_code == 200
    assert r.json()["prompt"] == "提示词正文"


def test_api_story_prompt_404(tmp_db):
    assert client.get("/api/story-prompt/99999").status_code == 404


def test_api_story_prompt_path_does_not_collide_with_story_endpoint(tmp_db):
    """GET /api/story/{deck_id}/{category} is registered first and would swallow
    /api/story/{id}/prompt as category='prompt' — hence the separate path."""
    story_id, _ = _make_story()
    r = client.get(f"/api/story-prompt/{story_id}")
    assert r.status_code == 200
    assert "story_id" in r.json()


# ---------------------------------------------------------------------------
# Thumbs-down ratings (#712)
#
# `starred` carries all three states (1 good / 0 none / -1 bad) rather than
# gaining a sibling column. These tests pin the parts that are easy to get
# wrong: that a reason never outlives the thumbs-down it belongs to, and that
# the pre-#712 boolean API still works for clients that haven't reloaded.
# ---------------------------------------------------------------------------

def test_rating_bad_round_trips_with_reason(tmp_db):
    _, sentence_id = _make_story()

    result = database.set_sentence_rating(sentence_id, -1, "too_hard")
    assert result["rating"] == -1
    assert result["bad_reason"] == "too_hard"
    assert result["starred_at"]

    [s] = database.get_starred_sentences()
    assert s["id"] == sentence_id
    assert s["rating"] == -1
    assert s["bad_reason"] == "too_hard"


def test_rating_filter_separates_good_from_bad(tmp_db):
    deck_id = database.get_or_create_deck("StarDeck")
    _, good = _make_story(deck_id)
    _, bad = _make_story(deck_id, category="listening")
    database.set_sentence_rating(good, 1)
    database.set_sentence_rating(bad, -1, "unnatural")

    assert [s["id"] for s in database.get_starred_sentences(rating="good")] == [good]
    assert [s["id"] for s in database.get_starred_sentences(rating="bad")] == [bad]
    # Unfiltered returns both — tuning a prompt means reading them against each other.
    assert {s["id"] for s in database.get_starred_sentences()} == {good, bad}


def test_reason_is_cleared_when_rating_leaves_bad(tmp_db):
    """A stale 'too_hard' tag on a sentence Daniel later liked would be a lie
    to whoever reads the list while tuning a prompt."""
    _, sentence_id = _make_story()
    database.set_sentence_rating(sentence_id, -1, "too_hard")

    assert database.set_sentence_rating(sentence_id, 1)["bad_reason"] is None
    [s] = database.get_starred_sentences()
    assert s["rating"] == 1 and s["bad_reason"] is None

    # And clearing the rating entirely drops it too.
    database.set_sentence_rating(sentence_id, -1, "grammar")
    assert database.set_sentence_rating(sentence_id, 0)["bad_reason"] is None


def test_reason_is_optional_on_a_thumbs_down(tmp_db):
    """The rating saves on its own — the tag row is a follow-up that may be skipped."""
    _, sentence_id = _make_story()
    assert database.set_sentence_rating(sentence_id, -1)["rating"] == -1
    assert database.get_starred_sentences(rating="bad")[0]["bad_reason"] is None


def test_invalid_rating_and_reason_are_rejected(tmp_db):
    _, sentence_id = _make_story()
    with pytest.raises(ValueError):
        database.set_sentence_rating(sentence_id, 5)
    with pytest.raises(ValueError):
        database.set_sentence_rating(sentence_id, -1, "not-a-real-reason")


def test_legacy_boolean_helper_still_works(tmp_db):
    _, sentence_id = _make_story()
    assert database.set_sentence_starred(sentence_id, True)["starred"] == 1
    assert database.set_sentence_starred(sentence_id, False)["starred"] == 0


def test_api_rating_bad_with_reason(tmp_db):
    _, sentence_id = _make_story()

    r = client.post(f"/api/story-sentence/{sentence_id}/star",
                    json={"rating": -1, "bad_reason": "factual_error"})
    assert r.status_code == 200
    assert r.json()["rating"] == -1
    assert r.json()["bad_reason"] == "factual_error"

    body = client.get("/api/starred-sentences?rating=bad").json()["sentences"]
    assert [s["id"] for s in body] == [sentence_id]
    assert client.get("/api/starred-sentences?rating=good").json()["sentences"] == []


def test_api_still_accepts_the_pre_712_boolean_body(tmp_db):
    """An open tab running the old app.js must keep working across a deploy."""
    _, sentence_id = _make_story()
    assert client.post(f"/api/story-sentence/{sentence_id}/star",
                       json={"starred": True}).json()["starred"] == 1
    assert client.post(f"/api/story-sentence/{sentence_id}/star",
                       json={"starred": False}).json()["starred"] == 0


def test_api_rejects_invalid_rating_with_400(tmp_db):
    _, sentence_id = _make_story()
    assert client.post(f"/api/story-sentence/{sentence_id}/star",
                       json={"rating": 7}).status_code == 400
    assert client.post(f"/api/story-sentence/{sentence_id}/star",
                       json={"rating": -1, "bad_reason": "bogus"}).status_code == 400
    assert client.post(f"/api/story-sentence/{sentence_id}/star",
                       json={"rating": "abc"}).status_code == 400
