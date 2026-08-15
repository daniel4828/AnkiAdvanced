"""Tests for the in-app AI dictionary (issue #746).

The AI is stubbed at ai._call_api — the single choke point every provider
goes through (see tests/test_add_word.py for why not a provider client).
"""
import json

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
from unittest.mock import patch

import ai
import database
import main
import routes.dictionary

client = TestClient(main.app)


GOOD_RESULT = {
    "input_lang": "de",
    "kind": "phrase",
    "headline": "派任务",
    "headline_pinyin": "pài rènwu",
    "headline_de": "jemandem eine Aufgabe geben",
    "notes": "口语里最自然的说法是 派任务",
    "groups": [
        {
            "label": "assign (Verb)",
            "options": [
                {
                    "key": "a",
                    "zh": "派",
                    "pinyin": "pài",
                    "de": "beauftragen",
                    "usage": "sehr umgangssprachlich",
                    "register": "spoken_colloquial",
                    "recommended": True,
                    "example_zh": "老师又给我派任务了。",
                    "example_pinyin": "Lǎoshī yòu gěi wǒ pài rènwu le.",
                    "example_de": "Der Lehrer hat mir schon wieder eine Aufgabe gegeben.",
                }
            ],
        }
    ],
}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp database. Patch database.core.DB_PATH — the package-level
    name is only a copy (issue #615)."""
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    monkeypatch.setattr(routes.dictionary, "ai_disabled", lambda: False)
    return tmp_path


def _stub_response(result=GOOD_RESULT):
    return json.dumps(result, ensure_ascii=False)


def test_lookup_saves_and_returns_full_result(tmp_db):
    with patch.object(ai, "_call_api", return_value=_stub_response()):
        r = client.post("/api/dict/lookup", json={"query": "jemandem eine Aufgabe geben"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "jemandem eine Aufgabe geben"
    assert body["result"]["headline"] == "派任务"
    assert body["result"]["groups"][0]["options"][0]["zh"] == "派"
    assert "id" in body and "created_at" in body


def test_history_roundtrip_and_search(tmp_db):
    with patch.object(ai, "_call_api", return_value=_stub_response()):
        client.post("/api/dict/lookup", json={"query": "jemandem eine Aufgabe geben"})

    other = dict(GOOD_RESULT, headline="生态")
    with patch.object(ai, "_call_api", return_value=_stub_response(other)):
        client.post("/api/dict/lookup", json={"query": "ecology"})

    items = client.get("/api/dict/history").json()["items"]
    assert len(items) == 2
    assert {i["headline"] for i in items} == {"派任务", "生态"}

    # Search matches on query OR headline.
    by_query = client.get("/api/dict/history", params={"q": "Aufgabe"}).json()["items"]
    assert len(by_query) == 1 and by_query[0]["headline"] == "派任务"

    by_headline = client.get("/api/dict/history", params={"q": "生态"}).json()["items"]
    assert len(by_headline) == 1 and by_headline[0]["query"] == "ecology"

    no_match = client.get("/api/dict/history", params={"q": "nonexistent"}).json()["items"]
    assert no_match == []


def test_history_item_full_shape(tmp_db):
    with patch.object(ai, "_call_api", return_value=_stub_response()):
        created = client.post("/api/dict/lookup", json={"query": "test"}).json()

    r = client.get(f"/api/dict/history/{created['id']}")
    assert r.status_code == 200
    body = r.json()
    assert body["result"] == GOOD_RESULT
    assert body["query"] == "test"


def test_history_item_missing_is_404(tmp_db):
    assert client.get("/api/dict/history/999").status_code == 404


def test_delete_history_item(tmp_db):
    with patch.object(ai, "_call_api", return_value=_stub_response()):
        created = client.post("/api/dict/lookup", json={"query": "test"}).json()

    r = client.delete(f"/api/dict/history/{created['id']}")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    assert client.get(f"/api/dict/history/{created['id']}").status_code == 404


def test_delete_missing_history_item_is_404(tmp_db):
    """Deleting a nonexistent row must not pretend success (project rule:
    never fake a successful result)."""
    assert client.delete("/api/dict/history/999").status_code == 404


def test_empty_query_is_rejected(tmp_db):
    assert client.post("/api/dict/lookup", json={"query": "   "}).status_code == 400


def test_ai_disabled_is_rejected(tmp_db, monkeypatch):
    monkeypatch.setattr(routes.dictionary, "ai_disabled", lambda: True)
    r = client.post("/api/dict/lookup", json={"query": "test"})
    assert r.status_code == 400


def test_garbage_ai_response_fails_loudly_and_writes_nothing(tmp_db):
    """A model that answers in prose (or otherwise unparseable JSON) must
    surface as a server error, and — critically — never reach the database:
    a blank/garbage dictionary entry is worse than an error."""
    with patch.object(ai, "_call_api", return_value="Sorry, I cannot help with that."):
        r = client.post("/api/dict/lookup", json={"query": "test"})
    assert r.status_code == 500
    assert client.get("/api/dict/history").json()["items"] == []


def test_ai_response_missing_groups_is_rejected(tmp_db):
    bad = {"input_lang": "de", "kind": "phrase", "headline": "x"}
    with patch.object(ai, "_call_api", return_value=json.dumps(bad)):
        r = client.post("/api/dict/lookup", json={"query": "test"})
    assert r.status_code == 500
    assert client.get("/api/dict/history").json()["items"] == []


def test_ai_response_with_empty_zh_option_is_rejected(tmp_db):
    bad = {
        "input_lang": "de", "kind": "phrase", "headline": "x",
        "groups": [{"label": "g", "options": [{"key": "a", "zh": ""}]}],
    }
    with patch.object(ai, "_call_api", return_value=json.dumps(bad)):
        r = client.post("/api/dict/lookup", json={"query": "test"})
    assert r.status_code == 500
    assert client.get("/api/dict/history").json()["items"] == []


# ---------------------------------------------------------------------------
# ai.dictionary_lookup() itself
# ---------------------------------------------------------------------------

def test_dictionary_lookup_strips_code_fence():
    fenced = "```json\n" + json.dumps(GOOD_RESULT) + "\n```"
    with patch.object(ai, "_call_api", return_value=fenced):
        result, model = ai.dictionary_lookup("test")
    assert result["headline"] == "派任务"
    assert model == ai.DEFAULT_MODEL


def test_dictionary_lookup_raises_on_unparseable_response():
    with patch.object(ai, "_call_api", return_value="not json at all"):
        with pytest.raises(ValueError):
            ai.dictionary_lookup("test")


# ---------------------------------------------------------------------------
# /dict standalone page
# ---------------------------------------------------------------------------

def test_dict_page_is_served():
    r = client.get("/dict")
    assert r.status_code == 200
