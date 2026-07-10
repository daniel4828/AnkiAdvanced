import json
import sqlite3
from datetime import date, timedelta
from .core import get_db


# ---------------------------------------------------------------------------
# Stories & sentences
# ---------------------------------------------------------------------------

def get_active_story(date_str: str, category: str, deck_id: int, lang: str | None = None) -> dict | None:
    """Latest story for (date, category, deck_id) or None.

    When `lang` is given, only stories matching that lang are considered (a NULL
    lang column — a legacy row from before issue #436 — counts as 'zh'). This
    stops zh/fr stories on the same aggregate deck from shadowing each other.
    """
    conn = get_db()
    lang_clause = ""
    params = [date_str, category, deck_id]
    if lang is not None:
        lang_clause = " AND (lang = ? OR (lang IS NULL AND ? = 'zh'))"
        params += [lang, lang]
    row = conn.execute(
        f"""SELECT * FROM stories
           WHERE date = ? AND category = ? AND deck_id = ?{lang_clause}
           ORDER BY generated_at DESC LIMIT 1""",
        params,
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def has_story_history(deck_id: int, category: str) -> bool:
    """Return True if any story exists for this deck+category."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM stories WHERE deck_id = ? AND category = ? LIMIT 1",
        (deck_id, category),
    ).fetchone()
    conn.close()
    return row is not None


def get_latest_story(deck_id: int, category: str, lang: str | None = None) -> dict | None:
    """Most recent story for (deck_id, category), regardless of date.

    See get_active_story() for the lang-matching rule (NULL lang == 'zh').
    """
    conn = get_db()
    lang_clause = ""
    params = [deck_id, category]
    if lang is not None:
        lang_clause = " AND (lang = ? OR (lang IS NULL AND ? = 'zh'))"
        params += [lang, lang]
    row = conn.execute(
        f"""SELECT * FROM stories WHERE deck_id = ? AND category = ?{lang_clause}
           ORDER BY generated_at DESC LIMIT 1""",
        params,
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def create_story(date_str: str, category: str, deck_id: int,
                 sentences: list[dict], prompt_text: str | None = None,
                 topic: str | None = None, gen_params: dict | None = None,
                 lang: str | None = None) -> int:
    """Always inserts a new story row. Returns story_id.

    Each sentence dict must have: position, sentence_zh, word_ids (list of entry IDs).
    Optional: sentence_en, sentence_de, sentence_fr.
    gen_params: the generation settings (mode/model/grammar/…) stored as JSON so the
    "Again" regeneration can reproduce the deck's style instead of a plain story.
    lang: target language of this story (issue #436); None is stored as-is (legacy
    behavior — callers that know the lang should always pass it).
    """
    conn = get_db()
    gen_params_json = json.dumps(gen_params, ensure_ascii=False) if gen_params else None
    cur = conn.execute(
        "INSERT INTO stories (date, category, deck_id, prompt_text, topic, gen_params, lang) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date_str, category, deck_id, prompt_text, topic, gen_params_json, lang),
    )
    story_id = cur.lastrowid
    for s in sentences:
        tokens_json = json.dumps(s["tokens"], ensure_ascii=False) if s.get("tokens") else None
        sent_cur = conn.execute(
            """INSERT INTO story_sentences
               (story_id, position, sentence_zh, sentence_en, sentence_de, sentence_fr, tokens, concept_en, concept_zh, reasoning_zh, source_url, context_de, source_title, source_name)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, s["position"], s["sentence_zh"],
             s.get("sentence_en", ""), s.get("sentence_de"), s.get("sentence_fr"), tokens_json,
             s.get("concept_en"), s.get("concept_zh"), s.get("reasoning_zh"), s.get("source_url"),
             s.get("context_de"), s.get("source_title"), s.get("source_name")),
        )
        sentence_id = sent_cur.lastrowid
        for word_id in s.get("word_ids", []):
            conn.execute(
                "INSERT INTO story_sentence_words (sentence_id, word_id) VALUES (?, ?)",
                (sentence_id, word_id),
            )
    conn.commit()
    conn.close()
    return story_id


def get_sentence_for_word(story_id: int, word_id: int) -> dict | None:
    """Return the first sentence (lowest position) in this story that contains word_id."""
    conn = get_db()
    row = conn.execute(
        """SELECT s.* FROM story_sentences s
           JOIN story_sentence_words sw ON sw.sentence_id = s.id
           WHERE s.story_id = ? AND sw.word_id = ?
           ORDER BY s.position ASC LIMIT 1""",
        (story_id, word_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _hydrate_sentence(conn, sent_row) -> dict:
    """Attach word_ids / words / tokens to a raw story_sentences row.

    Produces the same shape as get_story_sentences() entries, so the result
    carries German/French translations and tokens too.
    """
    word_rows = conn.execute(
        """SELECT e.id AS word_id, e.word_zh, e.definition
           FROM story_sentence_words sw
           JOIN entries e ON e.id = sw.word_id
           WHERE sw.sentence_id = ?
           ORDER BY sw.id""",
        (sent_row["id"],),
    ).fetchall()

    wlist = [{"word_id": w["word_id"], "word_zh": w["word_zh"], "definition": w["definition"]}
             for w in word_rows]
    d = dict(sent_row)
    d["word_ids"] = [w["word_id"] for w in wlist]
    d["words"] = wlist
    if wlist:
        d["word_zh"] = wlist[0]["word_zh"]
        d["definition"] = wlist[0]["definition"]
    raw_tokens = d.get("tokens")
    d["tokens"] = json.loads(raw_tokens) if raw_tokens else []
    return d


def get_latest_sentence_for_word(word_id: int) -> dict | None:
    """Return the most recent sentence (across all stories) that contains word_id.

    Used as a fallback in mixed/"All" review when a cross-day card's word is not in
    the currently loaded story. The returned dict matches get_story_sentences() shape
    (word_ids, words, tokens), so it carries German/French translations too.
    """
    conn = get_db()
    sent_row = conn.execute(
        """SELECT ss.* FROM story_sentences ss
           JOIN story_sentence_words sw ON sw.sentence_id = ss.id
           JOIN stories s ON s.id = ss.story_id
           WHERE sw.word_id = ?
           ORDER BY s.generated_at DESC, ss.id DESC
           LIMIT 1""",
        (word_id,),
    ).fetchone()
    if sent_row is None:
        conn.close()
        return None
    d = _hydrate_sentence(conn, sent_row)
    conn.close()
    return d


# Sentinel category for single-sentence regenerations triggered by an "Again" rating.
# Stored as a story so it reuses story_sentences/translations/tokens, but kept under
# this category so get_active_story()/has_story_history() (which filter by the real
# category) never pick it up — it only surfaces via get_again_sentence_for_word().
AGAIN_CATEGORY = "again"


def store_again_sentence(deck_id: int, word_id: int, sentence: dict,
                         date_str: str) -> int:
    """Store one freshly-regenerated sentence for a word that was rated Again.

    `sentence` is a dict from ai.generate_story() (sentence_zh, sentence_en,
    sentence_de, tokens, …). Returns the new story id.
    """
    s = dict(sentence)
    s["position"] = 0
    s["word_ids"] = [word_id]
    return create_story(date_str, AGAIN_CATEGORY, deck_id, [s])


def get_again_sentence_for_word(word_id: int, date_str: str) -> dict | None:
    """Return the latest Again-regenerated sentence for this word on `date_str`, or None.

    Same shape as get_latest_sentence_for_word(). Scoped to one day so a fresh
    sentence is only reused within the day it was generated.
    """
    conn = get_db()
    sent_row = conn.execute(
        """SELECT ss.* FROM story_sentences ss
           JOIN story_sentence_words sw ON sw.sentence_id = ss.id
           JOIN stories s ON s.id = ss.story_id
           WHERE sw.word_id = ? AND s.date = ? AND s.category = ?
           ORDER BY s.generated_at DESC, ss.id DESC
           LIMIT 1""",
        (word_id, date_str, AGAIN_CATEGORY),
    ).fetchone()
    if sent_row is None:
        conn.close()
        return None
    d = _hydrate_sentence(conn, sent_row)
    conn.close()
    return d


def get_story_gen_params_for_word(word_id: int, date_str: str) -> dict | None:
    """Return the generation settings (gen_params) of the most recent real story
    today that contains this word, or None. Excludes the 'again' sentinel stories.

    Used by the Again regeneration to reproduce the deck story's style. Works for
    both per-category and unified stories because it matches by word, not deck.
    """
    conn = get_db()
    row = conn.execute(
        """SELECT s.gen_params FROM stories s
           JOIN story_sentences ss ON ss.story_id = s.id
           JOIN story_sentence_words sw ON sw.sentence_id = ss.id
           WHERE sw.word_id = ? AND s.date = ? AND s.category != ?
           ORDER BY s.generated_at DESC
           LIMIT 1""",
        (word_id, date_str, AGAIN_CATEGORY),
    ).fetchone()
    conn.close()
    if not row or not row["gen_params"]:
        return None
    try:
        return json.loads(row["gen_params"])
    except (ValueError, TypeError):
        return None


def get_story_sentences(story_id: int) -> list[dict]:
    """Return all sentences for a story, each with word_ids and words list."""
    conn = get_db()
    sent_rows = conn.execute(
        "SELECT * FROM story_sentences WHERE story_id = ? ORDER BY position",
        (story_id,),
    ).fetchall()

    word_rows = conn.execute(
        """SELECT sw.sentence_id, e.id AS word_id, e.word_zh, e.definition
           FROM story_sentence_words sw
           JOIN story_sentences ss ON ss.id = sw.sentence_id
           JOIN entries e ON e.id = sw.word_id
           WHERE ss.story_id = ?
           ORDER BY sw.sentence_id, sw.id""",
        (story_id,),
    ).fetchall()
    conn.close()

    words_by_sentence: dict[int, list] = {}
    for w in word_rows:
        words_by_sentence.setdefault(w["sentence_id"], []).append({
            "word_id": w["word_id"],
            "word_zh": w["word_zh"],
            "definition": w["definition"],
        })

    result = []
    for s in sent_rows:
        d = dict(s)
        wlist = words_by_sentence.get(s["id"], [])
        d["word_ids"] = [w["word_id"] for w in wlist]
        d["words"] = wlist
        # Legacy compat: expose word_zh / definition of first word for callers that still use them
        if wlist:
            d["word_zh"] = wlist[0]["word_zh"]
            d["definition"] = wlist[0]["definition"]
        raw_tokens = d.get("tokens")
        d["tokens"] = json.loads(raw_tokens) if raw_tokens else []
        result.append(d)
    return result


def get_story_position_map(deck_id: int, category: str, date_str: str,
                            lang: str | None = None) -> dict[int, int]:
    """Map word_id → story_sentences.position for today's active News-flow-style
    story (briefing/news/paste — issue #454), used to order the review queue to
    match the summary's reading order. Returns {} when there is no active story
    or its gen_params.mode isn't one of those three (e.g. plain story/kahneman
    modes, which don't have a meaningful "reading order" for review purposes).
    """
    story = get_active_story(date_str, category, deck_id, lang)
    if not story:
        return {}
    gen_params = story.get("gen_params")
    if not gen_params:
        return {}
    try:
        mode = json.loads(gen_params).get("mode")
    except (ValueError, TypeError):
        return {}
    if mode not in ("briefing", "news", "paste"):
        return {}

    conn = get_db()
    rows = conn.execute(
        """SELECT sw.word_id, ss.position
           FROM story_sentence_words sw
           JOIN story_sentences ss ON ss.id = sw.sentence_id
           WHERE ss.story_id = ?""",
        (story["id"],),
    ).fetchall()
    conn.close()
    return {r["word_id"]: r["position"] for r in rows}


def get_recent_story_keys(before_date: str, max_lookback_days: int = 14) -> list[dict]:
    """Morning pregen (issue #458): find the most recent day before `before_date`
    (looking back up to `max_lookback_days`) that had "real" stories, and return
    the one active (deck_id, category, lang) key per story that day, each with its
    parsed gen_params — so today's pregen can reproduce yesterday's actual usage
    instead of blindly generating for every leaf deck.

    "Real" story = gen_params is non-empty AND it has >= 2 sentences — this
    excludes the single-sentence "again" regeneration rows (see AGAIN_CATEGORY)
    without needing to special-case that category by name.

    Stories whose gen_params has origin == "pregen" are skipped everywhere —
    both when picking the target day and when collecting its keys. Otherwise
    pregen's own output becomes the next day's input and one accidental batch
    (or one accidental mode) self-perpetuates forever (issue #468). Only
    user-initiated generations are reproduced.

    Returns [] if no matching day is found in the lookback window.
    """
    before = date.fromisoformat(before_date)
    earliest = before - timedelta(days=max_lookback_days)

    conn = get_db()
    rows = conn.execute(
        """SELECT * FROM stories s
           WHERE s.date < ? AND s.date >= ? AND s.gen_params IS NOT NULL
             AND (SELECT COUNT(*) FROM story_sentences ss WHERE ss.story_id = s.id) >= 2
           ORDER BY s.date DESC, s.generated_at DESC""",
        (before_date, earliest.isoformat()),
    ).fetchall()
    conn.close()

    target_date = None
    seen: set[tuple] = set()
    result: list[dict] = []
    for row in rows:
        try:
            gen_params = json.loads(row["gen_params"])
        except (ValueError, TypeError):
            continue
        if gen_params.get("origin") == "pregen":
            continue
        if target_date is None:
            target_date = row["date"]  # most recent day with a user-initiated story
        elif row["date"] != target_date:
            break  # rows are date DESC — we're past the target day
        key = (row["deck_id"], row["category"], row["lang"])
        if key in seen:
            continue  # keep only the latest generated_at per key (rows are DESC-ordered)
        seen.add(key)
        result.append({
            "deck_id": row["deck_id"],
            "category": row["category"],
            "lang": row["lang"],
            "gen_params": gen_params,
        })
    return result


def get_due_cards_unified(deck_id: int, lang: str | None = None) -> list[dict]:
    """Collect due cards from all 3 categories for a unified story, deduplicated by word_id.
    Order matches review priority (state → category → due) so story sentence positions
    align with the order cards will be presented during the review session.
    `lang` restricts aggregation to leaf decks of that language (language tabs)."""
    from .cards import get_due_cards, get_due_cards_multi, get_descendant_leaf_deck_ids, get_preset_for_deck
    from .decks import get_deck

    def _leaf_ids(cat: str) -> list[int]:
        deck = get_deck(deck_id)
        if deck["category"] is None:
            return get_descendant_leaf_deck_ids(deck_id, cat, lang=lang)
        return [deck_id]

    preset = get_preset_for_deck(deck_id)
    order_str = preset.get("category_order", "listening,reading,creating")
    cat_order = {c.strip(): i for i, c in enumerate(order_str.split(","))}

    seen: set[int] = set()
    result: list[dict] = []
    for cat in ("listening", "reading", "creating"):
        ids = _leaf_ids(cat)
        if not ids:
            continue
        cards = (get_due_cards_multi(ids, cat) if len(ids) > 1
                 else get_due_cards(ids[0], cat))
        for c in cards:
            if c.get("note_type") == "sentence":
                continue
            if c["word_id"] not in seen:
                seen.add(c["word_id"])
                result.append(c)

    # Match review order: state priority → category order → due time
    result.sort(key=lambda c: (
        0 if c["state"] in ("learning", "relearn") else
        1 if c["state"] == "review" else 2,
        cat_order.get(c["category"], 99),
        c["due"],
    ))

    # Apply new_review_order so new cards are interleaved (or placed first/last)
    # to match the order the review session will present them.
    from .cards import _interleave_cards
    nr_o = preset.get("new_review_order_override") or preset.get("new_review_order", "mixed")
    lr_cards  = [c for c in result if c["state"] != "new"]
    new_cards = [c for c in result if c["state"] == "new"]
    if new_cards and lr_cards:
        if nr_o == "new_first":
            result = new_cards + lr_cards
        elif nr_o == "reviews_first":
            result = lr_cards + new_cards
        else:  # mixed
            result = _interleave_cards(lr_cards, new_cards)

    return result
