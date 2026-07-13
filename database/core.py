import json
import os
import re
import sqlite3
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("DB_PATH", "data/srs.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")

DAY_CUTOFF_HOUR = 4  # New day starts at 4am, like Anki


def anki_today() -> date:
    """Return today's date using 4am as the day boundary (like Anki).

    Between midnight and 3:59am, returns yesterday's date so that late-night
    review sessions still count as the previous calendar day.
    """
    now = datetime.now()
    if now.hour < DAY_CUTOFF_HOUR:
        return (now - timedelta(days=1)).date()
    return now.date()


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _existing_tables(conn) -> set:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def init_db() -> None:
    os.makedirs("data", exist_ok=True)
    with open(SCHEMA_PATH, "r") as f:
        schema = f.read()
    conn = get_db()

    # ── Phase 1: rename legacy tables BEFORE running schema.sql ────────────────
    # (schema.sql uses CREATE TABLE IF NOT EXISTS, so pre-existing tables survive)
    existing = _existing_tables(conn)

    _TABLE_RENAMES = [
        ("words",             "entries"),
        ("word_examples",     "entry_examples"),
        ("word_characters",   "entry_characters"),
        ("word_measure_words","entry_measure_words"),
        ("word_relations",    "entry_relations"),
        ("note_components",   "entry_components"),
        ("sentences",         "story_sentences"),
    ]
    for old, new in _TABLE_RENAMES:
        if old in existing and new not in existing:
            conn.execute(f"ALTER TABLE {old} RENAME TO {new}")
    conn.commit()

    # ── Phase 2: run schema.sql (creates any tables that don't exist yet) ───────
    conn.executescript(schema)
    conn.commit()

    # ── Phase 3: column migrations on existing databases ────────────────────────
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()}
    if "notes" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN notes TEXT")
    if "note_type" not in cols:
        conn.execute(
            "ALTER TABLE entries ADD COLUMN note_type TEXT NOT NULL DEFAULT 'vocabulary'"
        )
    if "register" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN register TEXT CHECK(register IN ('spoken', 'written', 'both', 'spoken_colloquial', 'spoken_neutral', 'neutral', 'formal_written', 'literary'))")
    else:
        # Fix old 3-value CHECK constraint → 6-value (SQLite requires table recreation)
        entries_sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='entries'"
        ).fetchone()["sql"]
        if entries_sql and "spoken_neutral" not in entries_sql:
            # SQLite FK tracking: renaming 'entries' makes child tables reference the
            # renamed name.  Instead: create new table, copy data, drop old, rename new.
            col_names = [r["name"] for r in conn.execute("PRAGMA table_info(entries)").fetchall()]
            cols_csv = ", ".join(col_names)
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.commit()
            conn.execute("""CREATE TABLE _entries_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    word_zh         TEXT NOT NULL UNIQUE,
                    pinyin          TEXT,
                    definition      TEXT,
                    pos             TEXT,
                    hsk_level       INTEGER,
                    traditional     TEXT,
                    definition_zh   TEXT,
                    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
                    source          TEXT NOT NULL DEFAULT 'kouyu',
                    notes           TEXT,
                    note_type       TEXT NOT NULL DEFAULT 'vocabulary',
                    source_sentence TEXT,
                    grammar_notes   TEXT,
                    register        TEXT CHECK(register IN ('spoken', 'written', 'both', 'spoken_colloquial', 'spoken_neutral', 'neutral', 'formal_written', 'literary'))
                )""")
            conn.execute(f"INSERT INTO _entries_new ({cols_csv}) SELECT {cols_csv} FROM entries")
            conn.execute("DROP TABLE entries")
            conn.execute("ALTER TABLE _entries_new RENAME TO entries")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.commit()

    if "date_yaml" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN date_yaml TEXT")
    if "definition_de" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN definition_de TEXT")
    if "definition_fr" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN definition_fr TEXT")
    if "source_sentence" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN source_sentence TEXT")
    if "lang" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
    if "grammar_notes" not in cols:
        conn.execute("ALTER TABLE entries ADD COLUMN grammar_notes TEXT")

    ex_cols = {r["name"] for r in conn.execute("PRAGMA table_info(entry_examples)").fetchall()}
    if "example_type" not in ex_cols:
        conn.execute("ALTER TABLE entry_examples ADD COLUMN example_type TEXT NOT NULL DEFAULT 'example'")
    if "example_en" not in ex_cols:
        conn.execute("ALTER TABLE entry_examples ADD COLUMN example_en TEXT")

    # Remove duplicate examples (keep lowest id per word+text pair)
    conn.execute("""DELETE FROM entry_examples WHERE id NOT IN (
        SELECT MIN(id) FROM entry_examples GROUP BY word_id, example_zh
    )""")

    # Migrate compounds from JSON column → character_compounds relational table
    import json as _json_local
    chars_with_json = conn.execute(
        "SELECT id, compounds FROM characters WHERE compounds IS NOT NULL AND compounds != ''"
    ).fetchall()
    for ch in chars_with_json:
        try:
            clist = _json_local.loads(ch["compounds"])
            for pos, c in enumerate(clist):
                zh = (c.get("simplified") or c.get("zh") or c.get("compound") or "").strip()
                if zh:
                    conn.execute(
                        """INSERT OR IGNORE INTO character_compounds
                           (char_id, compound_zh, pinyin, meaning, position)
                           VALUES (?, ?, ?, ?, ?)""",
                        (ch["id"], zh, c.get("pinyin"), c.get("meaning"), pos),
                    )
        except Exception:
            pass

    conn.execute("""CREATE TABLE IF NOT EXISTS api_call_log (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        called_at     TEXT NOT NULL DEFAULT (datetime('now')),
        model         TEXT NOT NULL,
        input_tokens  INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        purpose       TEXT NOT NULL DEFAULT 'story'
    )""")
    api_call_log_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_call_log)").fetchall()}
    if "cached_input_tokens" not in api_call_log_cols:
        conn.execute("ALTER TABLE api_call_log ADD COLUMN cached_input_tokens INTEGER NOT NULL DEFAULT 0")
    if "action_id" not in api_call_log_cols:
        conn.execute("ALTER TABLE api_call_log ADD COLUMN action_id TEXT")
    if "action_label" not in api_call_log_cols:
        conn.execute("ALTER TABLE api_call_log ADD COLUMN action_label TEXT")
    if "prompt" not in api_call_log_cols:
        conn.execute("ALTER TABLE api_call_log ADD COLUMN prompt TEXT")

    story_cols = {r["name"] for r in conn.execute("PRAGMA table_info(stories)").fetchall()}
    if "prompt_text" not in story_cols:
        conn.execute("ALTER TABLE stories ADD COLUMN prompt_text TEXT")
    if "topic" not in story_cols:
        conn.execute("ALTER TABLE stories ADD COLUMN topic TEXT")
    if "gen_params" not in story_cols:
        conn.execute("ALTER TABLE stories ADD COLUMN gen_params TEXT")
    if "lang" not in story_cols:
        # NULL = legacy row generated before multi-language support (issue #436);
        # treated as 'zh' everywhere stories are looked up by lang.
        conn.execute("ALTER TABLE stories ADD COLUMN lang TEXT")
    _migrate_stories_category(conn)
    ss_cols = {r["name"] for r in conn.execute("PRAGMA table_info(story_sentences)").fetchall()}
    if "sentence_de" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN sentence_de TEXT")
    if "sentence_fr" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN sentence_fr TEXT")
    if "tokens" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN tokens TEXT")
    if "concept_en" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN concept_en TEXT")
    if "concept_zh" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN concept_zh TEXT")
    if "reasoning_zh" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN reasoning_zh TEXT")
    if "source_url" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN source_url TEXT")
    if "context_de" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN context_de TEXT")
    if "source_title" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN source_title TEXT")
    if "source_name" not in ss_cols:
        conn.execute("ALTER TABLE story_sentences ADD COLUMN source_name TEXT")
    if "word_id" in ss_cols:
        _migrate_story_sentences_multi_word(conn)
    existing_tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    if "story_sentence_words" not in existing_tables:
        conn.execute("""CREATE TABLE story_sentence_words (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sentence_id INTEGER NOT NULL REFERENCES story_sentences(id) ON DELETE CASCADE,
            word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            UNIQUE(sentence_id, word_id)
        )""")
        conn.commit()

    deck_cols = {r["name"] for r in conn.execute("PRAGMA table_info(decks)").fetchall()}
    if "deleted_at" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN deleted_at TEXT")
    if "new_review_order_override" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN new_review_order_override TEXT")
    if "bury_quick_mode" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN bury_quick_mode TEXT NOT NULL DEFAULT 'all'")
    if "lang" not in deck_cols:
        conn.execute("ALTER TABLE decks ADD COLUMN lang TEXT NOT NULL DEFAULT 'zh'")
    card_cols = {r["name"] for r in conn.execute("PRAGMA table_info(cards)").fetchall()}
    if "deleted_at" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN deleted_at TEXT")
    if "pre_suspend_state" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN pre_suspend_state TEXT")
    # Leech tracking: Again presses during learning, + a flag marking leech suspends.
    # Absence of is_leech signals a fresh install of this feature → run the one-time
    # historical backfill below (after preset thresholds are migrated).
    need_leech_backfill = "is_leech" not in card_cols
    if "learning_again_count" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN learning_again_count INTEGER NOT NULL DEFAULT 0")
    if "is_leech" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN is_leech INTEGER NOT NULL DEFAULT 0")

    # FSRS memory state. Absence of `stability` signals a pre-FSRS database →
    # seed existing review/relearn cards from their SM-2 interval/ease below.
    need_fsrs_seed = "stability" not in card_cols
    if "stability" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN stability REAL")
    if "difficulty" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN difficulty REAL")
    if "last_review" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN last_review TEXT")
    if "next_note" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN next_note TEXT")
    # Graduation probation: learning/relearn cards that finished their steps
    # stay in that state (probation=1) until they survive an interval of
    # >= learned_interval days; only then do they become 'review' cards.
    # Absence of the column signals a fresh install of this feature → run the
    # one-time backfill below that pulls existing "young review" cards
    # (interval below learned_interval) back into probation.
    need_probation_backfill = "probation" not in card_cols
    if "probation" not in card_cols:
        conn.execute("ALTER TABLE cards ADD COLUMN probation INTEGER NOT NULL DEFAULT 0")

    # review_log: per-review timing + card state at review time (for calendar heatmap stats)
    rl_cols = {r["name"] for r in conn.execute("PRAGMA table_info(review_log)").fetchall()}
    if "duration_ms" not in rl_cols:
        conn.execute("ALTER TABLE review_log ADD COLUMN duration_ms INTEGER")
    if "state" not in rl_cols:
        conn.execute("ALTER TABLE review_log ADD COLUMN state TEXT")
    if "last_interval" not in rl_cols:
        conn.execute("ALTER TABLE review_log ADD COLUMN last_interval INTEGER")

    preset_cols = {r["name"] for r in conn.execute("PRAGMA table_info(deck_presets)").fetchall()}
    if "new_gather_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_gather_order TEXT NOT NULL DEFAULT 'ascending_position'")
        # Map legacy insertion_order: random → random_cards, sequential → ascending_position
        if "insertion_order" in preset_cols:
            conn.execute("""UPDATE deck_presets SET new_gather_order =
                CASE insertion_order WHEN 'random' THEN 'random_cards' ELSE 'ascending_position' END""")
    if "new_sort_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_sort_order TEXT NOT NULL DEFAULT 'card_type_gathered'")
    if "new_review_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_review_order TEXT NOT NULL DEFAULT 'mixed'")
    if "interday_learning_review_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN interday_learning_review_order TEXT NOT NULL DEFAULT 'mixed'")
    if "review_sort_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN review_sort_order TEXT NOT NULL DEFAULT 'due_random'")
    if "bury_new_siblings" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_new_siblings INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_review_siblings INTEGER NOT NULL DEFAULT 0")
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_interday_siblings INTEGER NOT NULL DEFAULT 0")
        # Migrate from legacy bury_siblings
        if "bury_siblings" in preset_cols:
            conn.execute("""UPDATE deck_presets SET
                bury_new_siblings      = bury_siblings,
                bury_review_siblings   = bury_siblings,
                bury_interday_siblings = bury_siblings""")
    if "bury_quick_mode" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN bury_quick_mode TEXT NOT NULL DEFAULT 'all'")
    if "category_order" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN category_order TEXT NOT NULL DEFAULT 'listening,reading,creating'")
    if "new_review_order_override" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN new_review_order_override TEXT")
    if "sibling_separation" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN sibling_separation INTEGER NOT NULL DEFAULT 3")
    if "sibling_factor" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN sibling_factor REAL NOT NULL DEFAULT 0.2")
    if "learning_leech_threshold" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN learning_leech_threshold INTEGER NOT NULL DEFAULT 6")
        # Lower the review-leech threshold from the legacy default (8) to the new
        # default (3) for presets that never tuned it away from 8.
        conn.execute("UPDATE deck_presets SET leech_threshold = 3 WHERE leech_threshold = 8")
    if "desired_retention" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN desired_retention REAL NOT NULL DEFAULT 0.9")
    if "maximum_interval" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN maximum_interval INTEGER NOT NULL DEFAULT 36500")
    if "fsrs_weights" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN fsrs_weights TEXT")
    if "enable_fsrs" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN enable_fsrs INTEGER NOT NULL DEFAULT 1")
    if "learning_hard_1d" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN learning_hard_1d INTEGER NOT NULL DEFAULT 1")
    if "learning_hard_days" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN learning_hard_days REAL NOT NULL DEFAULT 1")
    if "learned_interval" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN learned_interval INTEGER NOT NULL DEFAULT 4")
    if "enable_probation" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN enable_probation INTEGER NOT NULL DEFAULT 1")
    if "reading_enabled" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN reading_enabled INTEGER NOT NULL DEFAULT 0")
    if "autoplay_delay_ms" not in preset_cols:
        conn.execute("ALTER TABLE deck_presets ADD COLUMN autoplay_delay_ms INTEGER NOT NULL DEFAULT 1000")

    conn.execute("""CREATE TABLE IF NOT EXISTS preset_category_overrides (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        preset_id           INTEGER NOT NULL REFERENCES deck_presets(id) ON DELETE CASCADE,
        category            TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
        new_per_day         INTEGER,
        reviews_per_day     INTEGER,
        learning_steps      TEXT,
        graduating_interval INTEGER,
        easy_interval       INTEGER,
        relearning_steps    TEXT,
        minimum_interval    INTEGER,
        leech_threshold     INTEGER,
        learning_leech_threshold INTEGER,
        leech_action        TEXT CHECK(leech_action IN ('suspend', 'tag')),
        UNIQUE(preset_id, category)
    )""")
    override_cols = {r["name"] for r in conn.execute("PRAGMA table_info(preset_category_overrides)").fetchall()}
    if "learning_leech_threshold" not in override_cols:
        conn.execute("ALTER TABLE preset_category_overrides ADD COLUMN learning_leech_threshold INTEGER")

    # ── One-time historical leech backfill ─────────────────────────────────────
    # Retroactively flag cards that already exceed their deck's leech thresholds,
    # so past Again presses count too. Runs once, when is_leech is first added.
    if need_leech_backfill:
        # Reconstruct learning Again presses from the review log (rating=1 while the
        # card was in new/learning/relearn). Logs with NULL state can't be classified
        # and are skipped — review-state lapses are already tracked in cards.lapses.
        conn.execute(
            """UPDATE cards SET learning_again_count = (
                   SELECT COUNT(*) FROM review_log r
                   WHERE r.card_id = cards.id AND r.rating = 1
                     AND r.state IN ('new', 'learning', 'relearn')
               )"""
        )
        # Suspend + flag every active card already over either threshold.
        conn.execute(
            """UPDATE cards
                  SET pre_suspend_state = state, state = 'suspended', is_leech = 1
                WHERE state != 'suspended' AND deleted_at IS NULL
                  AND id IN (
                      SELECT c.id FROM cards c
                      JOIN decks d ON d.id = c.deck_id
                      JOIN deck_presets p ON p.id = d.preset_id
                      WHERE c.lapses >= p.leech_threshold
                         OR c.learning_again_count >= p.learning_leech_threshold
                  )"""
        )

    # ── One-time graduation-probation backfill ────────────────────────────────
    # Before probation existed, cards graduated to 'review' immediately, so many
    # "young" review cards sit below their deck's learned_interval without ever
    # having survived such an interval. Pull them back into probation (relearn +
    # probation=1) so they follow the new rule: they only truly graduate — and
    # only start counting Again as a lapse — after surviving a >= learned_interval
    # gap. Interval / FSRS memory / lapses are preserved; only state changes.
    if need_probation_backfill:
        conn.execute(
            """UPDATE cards
                  SET state = 'relearn', probation = 1, step_index = 0
                WHERE state = 'review' AND deleted_at IS NULL
                  AND id IN (
                      SELECT c.id FROM cards c
                      JOIN decks d ON d.id = c.deck_id
                      JOIN deck_presets p ON p.id = d.preset_id
                      WHERE c.interval < p.learned_interval
                  )"""
        )

    # ── One-time FSRS seeding ──────────────────────────────────────────────────
    # Convert existing SM-2 state into FSRS memory state so no progress is lost:
    #   stability  ← current interval (already ≈ days-to-90%-retention)
    #   difficulty ← mapped from ease (low ease = hard card = high difficulty)
    # last_review stays NULL; the first FSRS review then assumes an on-time
    # elapsed (= interval) and self-corrects from there.
    if need_fsrs_seed:
        conn.execute(
            """UPDATE cards
               SET stability  = MAX(interval, 0.5),
                   difficulty = MAX(1.0, MIN(10.0, 11.0 - (ease - 1.3) * 6.5))
               WHERE state IN ('review', 'relearn') AND stability IS NULL"""
        )

    # Normalize legacy due values: learning/relearn cards whose due datetime
    # falls on a future Anki day should store just the date (no time component).
    # This matches the new _smart_due() rule in srs.py.
    today_str = anki_today().isoformat()
    conn.execute(
        """UPDATE cards
           SET due = substr(due, 1, 10)
           WHERE state IN ('learning', 'relearn')
             AND due LIKE '%T%'
             AND substr(due, 1, 10) > ?""",
        (today_str,),
    )

    conn.commit()

    # Ensure presets + default deck exist
    _ensure_presets(conn)
    preset_id = conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()["id"]
    all_id = _ensure_deck(conn, "All", parent_id=None, preset_id=preset_id)
    # Migrate any pre-existing root decks (other than "All") to be children of "All".
    # Done one-by-one to handle cases where a same-named deck already exists under "All"
    # (which would cause a UNIQUE(name, parent_id) violation on a bulk UPDATE).
    root_decks = conn.execute(
        "SELECT id, name FROM decks WHERE parent_id IS NULL AND id != ? AND deleted_at IS NULL",
        (all_id,),
    ).fetchall()
    for deck in root_decks:
        already_child = conn.execute(
            "SELECT id FROM decks WHERE name = ? AND parent_id = ?",
            (deck["name"], all_id),
        ).fetchone()
        if already_child:
            # A deck with the same name already lives under "All" — re-point any cards
            # that reference this orphaned root deck, then delete it.
            conn.execute(
                "UPDATE cards SET deck_id = ? WHERE deck_id = ?",
                (already_child["id"], deck["id"]),
            )
            conn.execute("DELETE FROM decks WHERE id = ?", (deck["id"],))
        else:
            conn.execute(
                "UPDATE decks SET parent_id = ? WHERE id = ?",
                (all_id, deck["id"]),
            )
    # Remove the unused "Default" deck if it exists and has no cards
    default_row = conn.execute("SELECT id FROM decks WHERE name = 'Default'").fetchone()
    if default_row:
        has_cards = conn.execute(
            "SELECT 1 FROM cards WHERE deck_id = ? LIMIT 1", (default_row["id"],)
        ).fetchone()
        if not has_cards:
            conn.execute("DELETE FROM decks WHERE id = ?", (default_row["id"],))
    conn.commit()

    # Remove the legacy "Sentences" deck tree if it holds no cards (issue #394)
    _drop_sentences_decks(conn)
    conn.commit()

    # pregen_config seeding (issue #473): when the table is first created, seed
    # the actual usage — News flow every morning for the aggregate "All" deck's
    # listening + creating categories. No-op when no root deck named "All"
    # exists (fresh installs, dev databases).
    if "pregen_config" not in existing:
        all_deck = conn.execute(
            "SELECT id FROM decks WHERE name = 'All' AND parent_id IS NULL "
            "AND deleted_at IS NULL LIMIT 1"
        ).fetchone()
        if all_deck:
            for cat in ("listening", "creating"):
                conn.execute(
                    """INSERT OR IGNORE INTO pregen_config
                       (deck_id, category, lang, mode, max_hsk)
                       VALUES (?, ?, 'zh', 'briefing', 2)""",
                    (all_deck["id"], cat))
            conn.commit()

    # podcast_config seeding (issue #479): when the table is first created,
    # seed the settings Daniel asked for so the crawler works out of the box
    # without a settings UI (that's a follow-up issue). channel_url was the
    # original (now-retired, #497) YouTube source.
    if "podcast_config" not in existing:
        seed = {
            "email_to": "u82g@outlook.com",
            "detail_level": "detailed",
            "enabled": "1",
        }
        for k, v in seed.items():
            conn.execute(
                "INSERT OR IGNORE INTO podcast_config (key, value) VALUES (?, ?)", (k, v))
        conn.commit()

    # whisper_fallback seeding (issue #485): added after podcast_config already
    # existed in production, so it can't rely on the "table just created"
    # branch above — INSERT OR IGNORE unconditionally on every startup so
    # existing installs (including production) pick it up without a migration.
    conn.execute(
        "INSERT OR IGNORE INTO podcast_config (key, value) VALUES ('whisper_fallback', '1')")
    conn.commit()

    # transcriber (#486) / whisper_max_minutes (#495) seeding: same pattern as
    # whisper_fallback above — unconditional INSERT OR IGNORE so existing
    # installs pick the new keys up without a migration. 'auto' means
    # NotebookLM (free) first, then Whisper (paid) as fallback; see
    # podcast._resolve_transcriber. whisper_max_minutes gates the paid
    # Whisper path to short episodes (Daniel only pays for the 10-15min
    # 早咖啡-style dailies; it replaced the never-matching title filter).
    conn.execute(
        "INSERT OR IGNORE INTO podcast_config (key, value) VALUES ('transcriber', 'auto')")
    conn.execute(
        "INSERT OR IGNORE INTO podcast_config (key, value) VALUES ('whisper_max_minutes', '30')")
    conn.commit()

    # summarizer (#510) seeding: same unconditional INSERT OR IGNORE pattern.
    # 'auto' means the free NotebookLM chat.ask summary is tried first (when
    # credentials are present), falling back to the gpt/DeepSeek API chain;
    # see podcast.summarize.
    conn.execute(
        "INSERT OR IGNORE INTO podcast_config (key, value) VALUES ('summarizer', 'auto')")
    conn.commit()

    # feeds (#497) seeding: RSS direct-link sources replacing the dead
    # YouTube channel_url (YouTube started bot-verifying the server's IP with
    # no Cookie fix that stuck, #491/#497). Same unconditional INSERT OR
    # IGNORE pattern as the keys above, seeded once with Daniel's two
    # subscriptions; existing installs pick it up on next startup without a
    # data migration.
    conn.execute(
        "INSERT OR IGNORE INTO podcast_config (key, value) VALUES ('feeds', ?)",
        (json.dumps([
            "https://www.ximalaya.com/album/51076156.xml",
            "https://feeds.fireside.fm/shengdongjixi/rss",
        ]),),
    )
    conn.commit()

    # podcast_feeds migration (issue #502): one-time move of the JSON array
    # in podcast_config.feeds into the new per-feed table (per-feed
    # auto_process toggle). Idempotent: only runs while podcast_feeds is
    # still empty, so it never re-inserts/duplicates on later startups (and
    # never overwrites feeds added/edited via the UI afterwards). Daniel's
    # existing 声动早咖啡 subscription (ximalaya) keeps its current
    # fully-automatic behavior; any other legacy feed defaults to manual.
    # title is left NULL here (no network request at startup) — the next
    # crawl (fetch_new_videos) backfills it from the RSS channel <title>.
    feeds_row_count = conn.execute("SELECT COUNT(*) FROM podcast_feeds").fetchone()[0]
    if feeds_row_count == 0:
        try:
            legacy_feeds = json.loads(conn.execute(
                "SELECT value FROM podcast_config WHERE key = 'feeds'"
            ).fetchone()[0])
        except (TypeError, ValueError, AttributeError):
            legacy_feeds = []
        for url in legacy_feeds:
            auto_process = 1 if "ximalaya" in url else 0
            conn.execute(
                "INSERT OR IGNORE INTO podcast_feeds (url, auto_process) VALUES (?, ?)",
                (url, auto_process),
            )
        conn.commit()

    # podcast_episodes column migrations (issue #486 transcript_source; #497
    # audio_url/duration_seconds for the RSS-direct pipeline).
    if "podcast_episodes" in existing:
        pe_cols = {r["name"] for r in conn.execute("PRAGMA table_info(podcast_episodes)").fetchall()}
        if "transcript_source" not in pe_cols:
            conn.execute("ALTER TABLE podcast_episodes ADD COLUMN transcript_source TEXT")
        if "audio_url" not in pe_cols:
            conn.execute("ALTER TABLE podcast_episodes ADD COLUMN audio_url TEXT")
        if "duration_seconds" not in pe_cols:
            conn.execute("ALTER TABLE podcast_episodes ADD COLUMN duration_seconds INTEGER")
        conn.commit()

        # Purge stale legacy YouTube rows (#497): yt-dlp is retired, so any
        # row that never got a transcript is now permanently stuck (it can
        # never be retried successfully) and would just keep eating the
        # auto-retry pass's budget forever. YouTube video ids are always
        # exactly 11 chars of [A-Za-z0-9_-]; RSS guids never match that
        # (ximalaya uses "xmly_track_<digits>", fireside uses UUIDs) so this
        # is a safe, precise filter. Summarized rows are always kept — they
        # have a real transcript worth preserving even though the source is
        # dead. These episodes will re-enter cleanly via the new RSS feeds.
        stale = conn.execute(
            "SELECT id, video_id FROM podcast_episodes WHERE status != 'summarized'"
        ).fetchall()
        stale_ids = [r["id"] for r in stale if re.fullmatch(r"[A-Za-z0-9_-]{11}", r["video_id"] or "")]
        if stale_ids:
            conn.executemany(
                "DELETE FROM podcast_episodes WHERE id = ?", [(i,) for i in stale_ids])
            conn.commit()

    # One-time transcript normalization (#500): NotebookLM ASR output stored
    # before the fix is Traditional Chinese with per-character spacing —
    # rewrite existing rows with the same cleanup podcast._normalize_transcript
    # now applies on ingest (logic duplicated here because core.py must not
    # import podcast.py — podcast.py imports database). Idempotent: already
    # clean rows rewrite to themselves and are skipped.
    import re as _re
    try:
        from zhconv import convert as _zh_convert
    except ImportError:
        _zh_convert = None
    rows = conn.execute(
        "SELECT id, transcript_zh FROM podcast_episodes "
        "WHERE transcript_zh IS NOT NULL AND transcript_zh != ''"
    ).fetchall()
    for row in rows:
        cleaned = _re.sub(r"(?<=[一-鿿　-〿＀-￯])\s+|\s+(?=[一-鿿　-〿＀-￯])", "", row["transcript_zh"])
        if _zh_convert is not None:
            cleaned = _zh_convert(cleaned, "zh-cn")
        if cleaned != row["transcript_zh"]:
            conn.execute("UPDATE podcast_episodes SET transcript_zh = ? WHERE id = ?",
                         (cleaned, row["id"]))
    conn.commit()

    conn.close()


def _migrate_story_sentences_multi_word(conn: sqlite3.Connection) -> None:
    """Rebuild story_sentences to remove word_id column, create story_sentence_words."""
    conn.executescript("""
        PRAGMA foreign_keys=OFF;

        CREATE TABLE story_sentences_new (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            position    INTEGER NOT NULL,
            sentence_zh TEXT NOT NULL,
            sentence_en TEXT NOT NULL DEFAULT '',
            sentence_de TEXT,
            sentence_fr TEXT,
            UNIQUE(story_id, position)
        );

        INSERT INTO story_sentences_new (id, story_id, position, sentence_zh, sentence_en, sentence_de, sentence_fr)
        SELECT id, story_id, position, sentence_zh, sentence_en, sentence_de, sentence_fr
        FROM story_sentences;

        CREATE TABLE IF NOT EXISTS story_sentence_words (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            sentence_id INTEGER NOT NULL REFERENCES story_sentences(id) ON DELETE CASCADE,
            word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
            UNIQUE(sentence_id, word_id)
        );

        INSERT INTO story_sentence_words (sentence_id, word_id)
        SELECT id, word_id FROM story_sentences;

        DROP TABLE story_sentences;
        ALTER TABLE story_sentences_new RENAME TO story_sentences;

        PRAGMA foreign_keys=ON;
    """)
    conn.commit()


def _migrate_stories_category(conn: sqlite3.Connection) -> None:
    """Extend stories.category CHECK constraint to include 'unified' and 'again'."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='stories'"
    ).fetchone()
    sql = row["sql"] if row else ""
    if row and ("'unified'" not in (sql or "") or "'again'" not in (sql or "")):
        col_names = [r["name"] for r in conn.execute("PRAGMA table_info(stories)").fetchall()]
        cols_csv = ", ".join(col_names)
        conn.executescript(f"""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE _stories_new (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                category        TEXT NOT NULL
                    CHECK(category IN ('listening', 'reading', 'creating', 'unified', 'again')),
                deck_id         INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
                generated_at    TEXT NOT NULL DEFAULT (datetime('now')),
                prompt_text     TEXT,
                topic           TEXT,
                gen_params      TEXT
            );
            INSERT INTO _stories_new ({cols_csv}) SELECT {cols_csv} FROM stories;
            DROP TABLE stories;
            ALTER TABLE _stories_new RENAME TO stories;
            PRAGMA foreign_keys=ON;
        """)
        conn.commit()


def _drop_sentences_decks(conn: sqlite3.Connection) -> None:
    """One-time migration: delete the legacy Sentences deck tree (issue #394).

    Only deletes when the tree holds no cards, so a database that still has
    sentence cards parked there is left untouched.
    """
    sent = conn.execute(
        "SELECT id FROM decks WHERE name = 'Sentences' AND parent_id IN "
        "(SELECT id FROM decks WHERE parent_id IS NULL) LIMIT 1"
    ).fetchone()
    if not sent:
        return
    deck_ids = [sent["id"]] + [
        r["id"] for r in conn.execute(
            "SELECT id FROM decks WHERE parent_id = ?", (sent["id"],)
        ).fetchall()
    ]
    placeholders = ",".join("?" * len(deck_ids))
    has_cards = conn.execute(
        f"SELECT 1 FROM cards WHERE deck_id IN ({placeholders}) LIMIT 1", deck_ids
    ).fetchone()
    if has_cards:
        return
    conn.execute(f"DELETE FROM decks WHERE id IN ({placeholders})", deck_ids)


def _ensure_presets(conn: sqlite3.Connection) -> None:
    """Seed the two built-in presets if they don't exist yet."""
    existing = {r["name"] for r in conn.execute("SELECT name FROM deck_presets").fetchall()}

    if "Default" not in existing:
        conn.execute(
            """INSERT INTO deck_presets (name, is_default) VALUES ('Default', 0)"""
        )

    if "Anki Default" not in existing:
        conn.execute(
            """INSERT INTO deck_presets
               (name, new_per_day, reviews_per_day,
                learning_steps, graduating_interval, easy_interval,
                relearning_steps, minimum_interval, insertion_order,
                bury_siblings, randomize_story_order, leech_threshold, leech_action, is_default)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            ("Anki Default", 9999, 9999, "11m 10m", 4, 9, "10", 1, "sequential", 1, 0, 8, "suspend"),
        )

    # Guarantee exactly one default
    if not conn.execute("SELECT id FROM deck_presets WHERE is_default = 1").fetchone():
        conn.execute("UPDATE deck_presets SET is_default = 1 WHERE name = 'Anki Default'")


def _ensure_default_preset(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()
    if row:
        return row["id"]
    _ensure_presets(conn)
    return conn.execute("SELECT id FROM deck_presets WHERE is_default = 1 LIMIT 1").fetchone()["id"]


def _ensure_deck(conn: sqlite3.Connection, name: str,
                 parent_id: int | None, preset_id: int,
                 category: str | None = None) -> int:
    row = conn.execute("SELECT id FROM decks WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute(
        "INSERT INTO decks (name, parent_id, preset_id, category) VALUES (?, ?, ?, ?)",
        (name, parent_id, preset_id, category),
    )
    return cur.lastrowid
