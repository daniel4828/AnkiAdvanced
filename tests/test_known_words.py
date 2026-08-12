"""Tests for the "already known" word list (#710).

Daniel knows plenty of words that never entered the collection; marking one
here is what stops zh_annotate from flagging it in every future summary. The
central claim under test is that this list and entries.word_zh are consulted
through ONE function (zh_annotate._known_words) — that union is why a marked
word disappears from the inline annotations and the HSK table at the same time.
"""

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
import database
import main
import zh_annotate

client = TestClient(main.app)


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp database. Patch database.core.DB_PATH, not database.DB_PATH
    — the latter is only a copy of the name (issue #615)."""
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


# --- database layer ---------------------------------------------------------

def test_add_and_query_known_word(tmp_db):
    database.add_known_word("经济衰退")
    assert database.known_words_exists(["经济衰退", "供应链"]) == {"经济衰退"}


def test_adding_twice_is_idempotent(tmp_db):
    """Meeting the same word in a second episode must not blow up."""
    database.add_known_word("经济衰退")
    database.add_known_word("经济衰退")
    assert [w["word_zh"] for w in database.list_known_words()] == ["经济衰退"]


def test_remove_known_word_reports_whether_it_was_there(tmp_db):
    database.add_known_word("经济衰退")
    assert database.remove_known_word("经济衰退") is True
    assert database.remove_known_word("经济衰退") is False
    assert database.list_known_words() == []


def test_known_words_exists_on_empty_input(tmp_db):
    assert database.known_words_exists([]) == set()


# --- the union that makes it work -------------------------------------------

def test_known_words_unions_collection_and_marked_list(monkeypatch):
    """The one place that answers "does Daniel know this word" must consult
    both sources — a word in either is known."""
    monkeypatch.setattr(database, "word_zh_exists", lambda words: {"就业"})
    monkeypatch.setattr(database, "known_words_exists", lambda words: {"供应链"})
    assert zh_annotate._known_words(["就业", "供应链", "衰退"]) == {"就业", "供应链"}


def test_marked_word_is_no_longer_annotated(tmp_db, monkeypatch):
    """End to end: mark a word, and the inline annotation stops appearing."""
    monkeypatch.setattr(zh_annotate, "_gloss_de", lambda w: f"DE:{w}")
    text = "经济衰退的风险"
    assert "经济衰退（" in zh_annotate.annotate_zh_summary(text)

    database.add_known_word("经济衰退")
    assert "经济衰退（" not in zh_annotate.annotate_zh_summary(text)


def test_marked_word_drops_out_of_the_word_table(tmp_db, monkeypatch):
    """extract_new_words feeds the HSK table under an episode — it reuses the
    same test, so the table and the annotations can't disagree."""
    monkeypatch.setattr(zh_annotate, "_gloss_de", lambda w: f"DE:{w}")
    database.add_known_word("经济衰退")
    words = [w["word"] for w in zh_annotate.extract_new_words("经济衰退的风险")]
    assert "经济衰退" not in words


# --- API --------------------------------------------------------------------

def test_api_add_list_and_delete(tmp_db):
    assert client.post("/api/known-words", json={"word": "经济衰退"}).status_code == 200
    listed = client.get("/api/known-words").json()["words"]
    assert [w["word_zh"] for w in listed] == ["经济衰退"]

    assert client.delete("/api/known-words/经济衰退").status_code == 200
    assert client.get("/api/known-words").json()["words"] == []


def test_api_rejects_empty_word(tmp_db):
    assert client.post("/api/known-words", json={"word": "  "}).status_code == 400


def test_api_delete_reports_a_miss(tmp_db):
    """A 404 means the frontend and the database disagree about the list —
    worth surfacing rather than answering "ok" to a no-op."""
    assert client.delete("/api/known-words/没有这个词").status_code == 404
