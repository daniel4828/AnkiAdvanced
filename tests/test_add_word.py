"""Tests for the in-app "add a word" flow (issue #627).

The AI is stubbed at ai._call_api — the single choke point every provider goes
through. Patching a provider client instead would silently stop working the
next time DEFAULT_MODEL changes (issue #615).
"""

from datetime import date, timedelta

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
from unittest.mock import patch

import ai
import database
import main
import routes.imports

client = TestClient(main.app)


ENTRY_YAML = """- type: word
  date: "08/06"
  simplified: 生态
  traditional: 生態
  pinyin: shēngtài
  english: ecology / ecosystem
  german: Ökologie / Ökosystem
  definition_zh: 生物与环境相互作用形成的系统
  pos: noun
  hsk: "5"
  register: formal_written
  note: |
    Ein Substantiv aus der Biologie.
  examples:
    - zh: 保护生态环境是我们的责任。
      pinyin: Bǎohù shēngtài huánjìng shì wǒmen de zérèn.
      english: Protecting the ecological environment is our responsibility.
      de: Die ökologische Umwelt zu schützen ist unsere Verantwortung.
  synonyms:
    - simplified: 环境
      pinyin: huánjìng
      meaning: Umwelt, Umgebung
  word_analyses:
    - char_only: 生
      pinyin: shēng
      hsk: "1"
"""


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp database. Patch database.core.DB_PATH — the package-level
    name is only a copy (issue #615)."""
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    # The route refuses to call the AI when DISABLE_AI is set; routes.utils
    # reads the env var at import time, so patch the resolved flag instead.
    monkeypatch.setattr(routes.imports, "ai_disabled", lambda: False)
    return tmp_path


def _run_add_word(word_zh, yaml_text=ENTRY_YAML, day=None):
    """POST the word and, if a background job started, wait for it to finish."""
    payload = {"word_zh": word_zh}
    if day:
        payload["day"] = day
    with patch.object(ai, "_call_api", return_value=yaml_text):
        r = client.post("/api/add-word-ai", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
        if "job_id" not in body:
            return body
        for _ in range(200):
            job = client.get(f"/api/add-word-ai/progress/{body['job_id']}").json()
            if job["status"] != "running":
                return {**body, "job": job}
            import time
            time.sleep(0.05)
        pytest.fail("add-word job never finished")


def _daily_leaf_decks(day=None):
    day = day or date.today().isoformat()
    deck_id = database.get_or_create_deck_path(f"Daily::{day}")
    return database.get_or_create_category_decks(deck_id, day)


def _today_leaf_decks():
    return _daily_leaf_decks()


def test_new_word_lands_in_todays_deck_due_today(tmp_db):
    result = _run_add_word("生态")
    assert result["job"]["status"] == "done", result["job"]
    assert result["job"]["summary"]["imported"] == 1

    entry = database.get_word_by_zh("生态")
    assert entry is not None
    assert entry["pinyin"] == "shēngtài"
    assert entry["definition_de"] == "Ökologie / Ökosystem"

    today = date.today().isoformat()
    leaf_ids = set(_today_leaf_decks().values())
    conn = database.get_db()
    cards = conn.execute(
        "SELECT category, deck_id, due, state FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry["id"],),
    ).fetchall()
    conn.close()

    assert {c["category"] for c in cards} == {"listening", "reading", "creating"}
    assert {c["deck_id"] for c in cards} <= leaf_ids
    # Suspended cards (reading, by importer default) carry no due date.
    assert all(c["due"] == today for c in cards if c["state"] != "suspended")


def test_full_entry_content_is_stored(tmp_db):
    """The point of the feature: the same richness as a hand-imported entry."""
    _run_add_word("生态")
    entry = database.get_word_by_zh("生态")
    detail = database.get_word_full(entry["id"])

    assert detail["examples"], "examples were not imported"
    assert detail["examples"][0]["example_de"].startswith("Die ökologische")
    assert any(r["related_zh"] == "环境" for r in detail["relations"])
    assert detail["notes"] and "Substantiv" in detail["notes"]


def test_known_word_is_reset_into_todays_deck_without_calling_the_ai(tmp_db):
    """Re-adding a studied word resets its cards to new and pulls them into
    today's deck (#675). cards has UNIQUE(word_id, category), so this moves the
    three cards it already owns — it never creates a fourth — and re-generating
    the entry would just burn an API call."""
    import importer

    other_deck = database.get_or_create_deck_path("Kouyu::Test")
    importer.import_yaml_content(ENTRY_YAML, other_deck)

    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        r = client.post("/api/add-word-ai", json={"word_zh": "生态"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "reset"
    assert any("Test" in name for name in body["previous_decks"])

    # The bug behind #643: the old /api/quick-add-word answered "✓ added" while
    # INSERT OR IGNORE silently dropped every card, so nothing reached the daily
    # deck. Prove the cards really moved — a success report is only worth
    # something if it matches reality.
    today = date.today().isoformat()
    leaf_ids = set(_today_leaf_decks().values())
    conn = database.get_db()
    rows = conn.execute(
        "SELECT deck_id, state, due FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (body["entry_id"],),
    ).fetchall()
    conn.close()
    assert rows and {r["deck_id"] for r in rows} == leaf_ids
    assert all(r["state"] == "new" and r["due"] == today for r in rows)
    assert body["cards_moved"] == len(rows)


def test_reset_clears_fsrs_progress_and_reports_what_it_discarded(tmp_db):
    """The reset is irreversible — stability/difficulty/interval/lapses are the
    word's whole memory model. The response must name the cost (#675)."""
    import importer

    other_deck = database.get_or_create_deck_path("Kouyu::Test")
    importer.import_yaml_content(ENTRY_YAML, other_deck)
    entry_id = database.get_word_by_zh("生态")["id"]

    conn = database.get_db()
    conn.execute(
        """UPDATE cards SET state='review', repetitions=4, lapses=2, interval=30,
           stability=42.0, difficulty=6.5, last_review='2026-08-01', is_leech=1
           WHERE word_id=?""",
        (entry_id,),
    )
    conn.commit()
    n_cards = conn.execute(
        "SELECT COUNT(*) c FROM cards WHERE word_id=?", (entry_id,)
    ).fetchone()["c"]
    conn.close()

    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        body = client.post("/api/add-word-ai", json={"word_zh": "生态"}).json()

    assert body["status"] == "reset"
    assert body["reviews_discarded"] == 4 * n_cards

    conn = database.get_db()
    rows = conn.execute(
        "SELECT * FROM cards WHERE word_id=? AND deleted_at IS NULL", (entry_id,)
    ).fetchall()
    conn.close()
    for row in rows:
        assert row["state"] == "new"
        assert row["stability"] is None and row["difficulty"] is None
        assert row["repetitions"] == 0 and row["lapses"] == 0 and row["interval"] == 0
        assert row["last_review"] is None and row["is_leech"] == 0


def test_saved_word_is_promoted_into_todays_deck(tmp_db):
    """A word only staged in the Saved deck has no scheduling progress to lose,
    so adding it means promoting it — still without an AI call."""
    r = client.post("/api/save-word", json={"word_zh": "生态", "pinyin": "shēngtài"})
    assert r.json()["status"] == "saved"
    entry_id = r.json()["entry_id"]

    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        r = client.post("/api/add-word-ai", json={"word_zh": "生态"})
    assert r.json()["status"] == "promoted"

    today = date.today().isoformat()
    leaf_ids = set(_today_leaf_decks().values())
    conn = database.get_db()
    rows = conn.execute(
        "SELECT deck_id, state, due FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchall()
    conn.close()
    assert {r["deck_id"] for r in rows} == leaf_ids
    assert all(r["state"] == "new" and r["due"] == today for r in rows)


def test_tomorrow_lands_in_tomorrows_deck_due_tomorrow(tmp_db):
    """day='tomorrow' (#636): both the deck and the cards' due date move a day
    forward — a future-dated daily deck stays locked until its date arrives, so
    a card left due today would be unreachable."""
    result = _run_add_word("生态", day="tomorrow")
    assert result["job"]["status"] == "done", result["job"]

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert result["deck_path"] == f"Daily::{tomorrow}"

    entry = database.get_word_by_zh("生态")
    leaf_ids = set(_daily_leaf_decks(tomorrow).values())
    conn = database.get_db()
    cards = conn.execute(
        "SELECT deck_id, due, state FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry["id"],),
    ).fetchall()
    conn.close()

    assert {c["deck_id"] for c in cards} <= leaf_ids
    assert all(c["due"] == tomorrow for c in cards if c["state"] != "suspended")


def test_saved_word_promoted_to_tomorrow(tmp_db):
    r = client.post("/api/save-word", json={"word_zh": "生态", "pinyin": "shēngtài"})
    entry_id = r.json()["entry_id"]

    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        r = client.post("/api/add-word-ai", json={"word_zh": "生态", "day": "tomorrow"})
    assert r.json()["status"] == "promoted"

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    conn = database.get_db()
    rows = conn.execute(
        "SELECT deck_id, due FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchall()
    conn.close()
    assert {r["deck_id"] for r in rows} == set(_daily_leaf_decks(tomorrow).values())
    assert all(r["due"] == tomorrow for r in rows)


def test_invalid_day_is_rejected(tmp_db):
    r = client.post("/api/add-word-ai", json={"word_zh": "生态", "day": "next week"})
    assert r.status_code == 400


def test_non_chinese_input_is_rejected(tmp_db):
    r = client.post("/api/add-word-ai", json={"word_zh": "Ökologie"})
    assert r.status_code == 400


def test_empty_input_is_rejected(tmp_db):
    assert client.post("/api/add-word-ai", json={"word_zh": "   "}).status_code == 400


def test_ai_returning_prose_fails_the_job(tmp_db):
    """A model that answers in prose must surface as an error, not a silent
    no-op that leaves the user staring at an empty deck."""
    result = _run_add_word("生态", yaml_text="Sorry, I cannot help with that.")
    assert result["job"]["status"] == "error"
    assert database.get_word_by_zh("生态") is None


def test_offline_returns_explicit_error(tmp_db, monkeypatch):
    monkeypatch.setattr(routes.imports, "ai_disabled", lambda: True)
    r = client.post("/api/add-word-ai", json={"word_zh": "生态"})
    assert r.status_code == 503


def test_generate_word_entry_yaml_strips_markdown_fence():
    fenced = "Here you go:\n```yaml\n" + ENTRY_YAML + "```\n"
    with patch.object(ai, "_call_api", return_value=fenced):
        out = ai.generate_word_entry_yaml("生态")
    assert out.startswith("- type: word")
    assert "```" not in out


def test_generate_word_entry_yaml_raises_without_entry():
    with patch.object(ai, "_call_api", return_value="I don't know this word."):
        with pytest.raises(ValueError):
            ai.generate_word_entry_yaml("生态")


# ---------------------------------------------------------------------------
# Standalone /add page (#668)
# ---------------------------------------------------------------------------

def test_add_page_is_served_without_the_app_bundle():
    """The whole point of /add is opening instantly on the phone — pulling in
    the ~9000-line app.js would defeat it."""
    body = client.get("/add").text
    assert 'id="word"' in body
    assert "/static/shared.js" in body
    assert "/static/app.js" not in body  # a comment may mention it; a <script> must not


def test_add_word_pipeline_is_not_duplicated_in_app_js():
    """#643: adding a word must have exactly one client-side implementation.
    A second copy in app.js would drift from shared.js and every fix would
    silently have to be made twice."""
    import pathlib
    app_js = pathlib.Path("static/app.js").read_text(encoding="utf-8")
    shared_js = pathlib.Path("static/shared.js").read_text(encoding="utf-8")
    assert "async function addWordViaAi(" in shared_js
    assert "async function addWordViaAi(" not in app_js
    assert "async function api(" in shared_js
    assert "async function api(" not in app_js


def test_background_deck_refresh_keeps_the_current_view():
    """#695: generation finishes ~30s later, often mid-review. loadDecks() ends
    in showView('decks'), so refreshing the due counts must pass keepView —
    otherwise finishing a word throws the user back to the home screen."""
    import pathlib
    shared_js = pathlib.Path("static/shared.js").read_text(encoding="utf-8")
    app_js = pathlib.Path("static/app.js").read_text(encoding="utf-8")
    assert "loadDecks({ keepView: true })" in shared_js
    assert "if (!keepView) showView('decks');" in app_js


def test_add_page_uses_the_shared_endpoint():
    """Guards against the page growing its own add-word call."""
    import pathlib
    add_html = pathlib.Path("static/add.html").read_text(encoding="utf-8")
    assert "addWordViaAi(" in add_html
    assert "/api/add-word-ai" not in add_html


# ---------------------------------------------------------------------------
# ★ List: park a word in the Saved deck instead of a Daily deck (#677)
# ---------------------------------------------------------------------------

def _saved_deck_id():
    return database.get_or_create_saved_deck()


def test_list_generates_the_full_entry_but_parks_it_suspended(tmp_db):
    """day='list' still pays for a complete de-zh-bot entry — the word is just
    kept out of every review queue until promoted."""
    result = _run_add_word("生态", day="list")
    assert result["job"]["status"] == "done", result["job"]
    assert result["deck_path"] == "Saved"

    entry = database.get_word_by_zh("生态")
    conn = database.get_db()
    cards = conn.execute(
        "SELECT deck_id, state, due FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry["id"],),
    ).fetchall()
    examples = conn.execute(
        "SELECT COUNT(*) c FROM entry_examples WHERE word_id=?", (entry["id"],)
    ).fetchone()["c"]
    conn.close()

    assert cards, "no cards were created"
    assert {c["deck_id"] for c in cards} == {_saved_deck_id()}
    # `due` is NOT NULL in the schema — state='suspended' is what keeps a card
    # out of the queues, not its due date.
    assert all(c["state"] == "suspended" for c in cards)
    assert examples > 0, "list mode must still produce the full entry"


def test_listed_word_is_invisible_to_the_review_queue(tmp_db):
    """The whole point: a listed word must not turn up for review anywhere."""
    _run_add_word("生态", day="list")
    today_deck = database.get_or_create_deck_path(f"Daily::{date.today().isoformat()}")
    for category in ("reading", "listening", "creating"):
        r = client.get(f"/api/today/{today_deck}/{category}")
        assert r.status_code == 200
        assert r.json().get("card") is None


def test_listing_a_studied_word_suspends_it_without_discarding_progress(tmp_db):
    """Parking is not a reset: suspending already keeps the card out of every
    queue, and promoting later resets it anyway (#677)."""
    import importer

    other_deck = database.get_or_create_deck_path("Kouyu::Test")
    importer.import_yaml_content(ENTRY_YAML, other_deck)
    entry_id = database.get_word_by_zh("生态")["id"]
    conn = database.get_db()
    conn.execute(
        "UPDATE cards SET state='review', repetitions=3, stability=20.0 WHERE word_id=?",
        (entry_id,),
    )
    conn.commit()
    conn.close()

    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        body = client.post("/api/add-word-ai", json={"word_zh": "生态", "day": "list"}).json()

    assert body["status"] == "listed"
    assert body["reviews_discarded"] == 0
    conn = database.get_db()
    rows = conn.execute(
        "SELECT deck_id, state, stability FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchall()
    conn.close()
    assert {r["deck_id"] for r in rows} == {_saved_deck_id()}
    assert all(r["state"] == "suspended" for r in rows)
    assert all(r["stability"] == 20.0 for r in rows), "parking must not wipe FSRS state"


def test_listing_an_already_listed_word_is_reported_as_such(tmp_db):
    r = client.post("/api/save-word", json={"word_zh": "生态", "pinyin": "shēngtài"})
    assert r.json()["status"] == "saved"
    with patch.object(ai, "_call_api", side_effect=AssertionError("AI was called")):
        body = client.post("/api/add-word-ai", json={"word_zh": "生态", "day": "list"}).json()
    assert body["status"] == "already_listed"


def test_listed_word_can_be_promoted_into_a_daily_deck(tmp_db):
    """Browse's '→ Add to Daily' button must work on words added via ★ List —
    that round trip is the reason the feature exists."""
    _run_add_word("生态", day="list")
    entry_id = database.get_word_by_zh("生态")["id"]

    r = client.post(f"/api/saved/{entry_id}/promote")
    assert r.status_code == 200, r.text
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    assert r.json()["deck_path"] == f"Daily::{tomorrow}"

    conn = database.get_db()
    rows = conn.execute(
        "SELECT deck_id, state, due FROM cards WHERE word_id=? AND deleted_at IS NULL",
        (entry_id,),
    ).fetchall()
    conn.close()
    assert {r["deck_id"] for r in rows} == set(_daily_leaf_decks(tomorrow).values())
    assert all(r["state"] == "new" and r["due"] == tomorrow for r in rows)


def test_invalid_day_still_rejected_and_list_accepted(tmp_db):
    assert client.post("/api/add-word-ai",
                       json={"word_zh": "生态", "day": "nextweek"}).status_code == 400


def test_add_page_reads_word_and_day_from_the_url(tmp_db):
    """/add?word=生态&day=list (#686) — an iOS Shortcut can add a word from any
    app. Asserted on the page source because the behaviour lives in the page's
    own script; the browser round trip is covered manually."""
    import pathlib
    src = pathlib.Path("static/add.html").read_text(encoding="utf-8")
    assert "URLSearchParams" in src
    assert "params.get('word')" in src and "params.get('w')" in src
    assert "params.get('day')" in src
    # The word must be stripped from the URL after submitting, or a reload
    # (or iOS restoring tabs) silently spends another AI call on it.
    assert "history.replaceState" in src
