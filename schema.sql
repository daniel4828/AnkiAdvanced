-- Chinese SRS Database Schema

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- deck_presets
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS deck_presets (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    name                    TEXT NOT NULL,

    -- Daily limits
    new_per_day             INTEGER NOT NULL DEFAULT 20,
    reviews_per_day         INTEGER NOT NULL DEFAULT 100,

    -- Learning steps in minutes, space-separated e.g. "1 10"
    learning_steps          TEXT NOT NULL DEFAULT '1 10',

    -- Graduation intervals in days
    graduating_interval     INTEGER NOT NULL DEFAULT 1,
    easy_interval           INTEGER NOT NULL DEFAULT 4,

    -- Relearning steps in minutes, space-separated e.g. "10"
    relearning_steps        TEXT NOT NULL DEFAULT '10',

    -- Review scheduling
    minimum_interval        INTEGER NOT NULL DEFAULT 1,

    -- New card insertion order (legacy; superseded by new_gather_order)
    insertion_order         TEXT NOT NULL DEFAULT 'sequential'
                                CHECK(insertion_order IN ('sequential', 'random')),

    -- Mark one preset as the default for new decks
    is_default              INTEGER NOT NULL DEFAULT 0,

    -- Bury siblings (legacy; superseded by per-state options below)
    bury_siblings           INTEGER NOT NULL DEFAULT 1,

    -- Randomize word order when generating stories
    randomize_story_order   INTEGER NOT NULL DEFAULT 0,

    -- Leech settings
    leech_threshold         INTEGER NOT NULL DEFAULT 8,
    leech_action            TEXT NOT NULL DEFAULT 'suspend'
                                CHECK(leech_action IN ('suspend', 'tag')),

    -- ── Display Order ────────────────────────────────────────────────────────

    new_gather_order        TEXT NOT NULL DEFAULT 'ascending_position'
                                CHECK(new_gather_order IN (
                                    'deck', 'deck_random_notes',
                                    'ascending_position', 'descending_position',
                                    'random_notes', 'random_cards')),

    new_sort_order          TEXT NOT NULL DEFAULT 'card_type_gathered'
                                CHECK(new_sort_order IN (
                                    'card_type_gathered', 'gathered',
                                    'card_type_random', 'random_note_card_type', 'random')),

    new_review_order        TEXT NOT NULL DEFAULT 'mixed'
                                CHECK(new_review_order IN ('mixed', 'new_first', 'reviews_first')),

    interday_learning_review_order TEXT NOT NULL DEFAULT 'mixed'
                                CHECK(interday_learning_review_order IN (
                                    'mixed', 'learning_first', 'reviews_first')),

    review_sort_order       TEXT NOT NULL DEFAULT 'due_random'
                                CHECK(review_sort_order IN (
                                    'due_random', 'due_deck', 'deck_due',
                                    'ascending_intervals', 'descending_intervals',
                                    'ascending_ease', 'descending_ease',
                                    'relative_overdueness')),

    -- ── Burying ──────────────────────────────────────────────────────────────

    bury_new_siblings       INTEGER NOT NULL DEFAULT 0,
    bury_review_siblings    INTEGER NOT NULL DEFAULT 0,
    bury_interday_siblings  INTEGER NOT NULL DEFAULT 0,

    -- Quick-access bury mode overrides the three per-state options above:
    --   'all'    = bury all siblings (default)
    --   'none'   = bury no siblings
    --   'custom' = use bury_new/review/interday_siblings individually
    bury_quick_mode         TEXT NOT NULL DEFAULT 'all'
                                CHECK(bury_quick_mode IN ('all', 'none', 'custom'))
);

-- ---------------------------------------------------------------------------
-- decks
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS decks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    parent_id   INTEGER REFERENCES decks(id) ON DELETE CASCADE,
    preset_id   INTEGER NOT NULL REFERENCES deck_presets(id),
    -- NULL for parent decks; set for category leaf decks
    category    TEXT CHECK(category IN ('listening', 'reading', 'creating')),
    -- soft delete: set when moved to trash, hard-deleted after 30 days
    deleted_at  TEXT,
    UNIQUE(name, parent_id)
);

-- ---------------------------------------------------------------------------
-- words  (no deck_id — words are deck-agnostic)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS words (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_zh         TEXT NOT NULL UNIQUE,
    pinyin          TEXT,
    definition      TEXT,           -- English definition
    pos             TEXT,           -- part of speech
    hsk_level       INTEGER,        -- 1-6, NULL for 超纲
    traditional     TEXT,
    definition_zh   TEXT,
    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
    source          TEXT NOT NULL DEFAULT 'kouyu',
    notes           TEXT,           -- personal notes
    source_sentence TEXT,           -- original source-language sentence (e.g. German) for sentence notes
    grammar_notes   TEXT,           -- grammar explanation (e.g. grammar_de from YAML)
    note_type       TEXT NOT NULL DEFAULT 'vocabulary'
                        -- vocabulary | sentence | chengyu | ...
);

-- ---------------------------------------------------------------------------
-- word_examples
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id         INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    example_zh      TEXT NOT NULL,
    example_pinyin  TEXT,
    example_de      TEXT,
    position        INTEGER NOT NULL
);

-- ---------------------------------------------------------------------------
-- characters
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS characters (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    char            TEXT NOT NULL UNIQUE,
    traditional     TEXT,
    pinyin          TEXT,
    hsk_level       INTEGER,
    etymology       TEXT,
    other_meanings  TEXT,   -- JSON array
    compounds       TEXT    -- JSON array
);

-- ---------------------------------------------------------------------------
-- word_characters  (junction table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS word_characters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id             INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    char_id             INTEGER NOT NULL REFERENCES characters(id),
    position            INTEGER NOT NULL,
    meaning_in_context  TEXT,
    UNIQUE(word_id, char_id)
);

-- ---------------------------------------------------------------------------
-- cards  (owns deck_id — one card per word per category, globally unique)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    deck_id     INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    category    TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    state       TEXT NOT NULL DEFAULT 'new'
                    CHECK(state IN ('new', 'learning', 'review', 'relearn', 'suspended')),

    -- due: ISO datetime for learning/relearn, ISO date for new/review
    due         TEXT NOT NULL DEFAULT (date('now')),

    step_index  INTEGER NOT NULL DEFAULT 0,
    interval    INTEGER NOT NULL DEFAULT 0,     -- days
    ease        REAL    NOT NULL DEFAULT 2.5,
    repetitions INTEGER NOT NULL DEFAULT 0,
    lapses      INTEGER NOT NULL DEFAULT 0,

    -- Temporary burial: card is hidden until this date (resets automatically next day)
    buried_until TEXT,

    -- soft delete: set when moved to trash, hard-deleted after 30 days
    deleted_at   TEXT,

    UNIQUE(word_id, category)
);

-- ---------------------------------------------------------------------------
-- review_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS review_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id         INTEGER NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
    reviewed_at     TEXT NOT NULL DEFAULT (datetime('now')),
    rating          INTEGER NOT NULL CHECK(rating IN (1, 2, 3, 4)),
    user_response   TEXT,       -- what the user typed (creating category)
    ai_score        INTEGER     -- future: AI evaluation score
);

-- ---------------------------------------------------------------------------
-- stories
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stories (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    date            TEXT NOT NULL,  -- YYYY-MM-DD
    category        TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    deck_id         INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    generated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    -- NO unique constraint: multiple stories per (date, category, deck) allowed
    -- active story = latest generated_at
    -- stories are NEVER auto-deleted
);

-- ---------------------------------------------------------------------------
-- sentences
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sentences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    sentence_zh TEXT NOT NULL,
    sentence_en TEXT NOT NULL,
    UNIQUE(story_id, word_id),
    UNIQUE(story_id, position)
);

-- ---------------------------------------------------------------------------
-- api_call_log
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS api_call_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at       TEXT NOT NULL DEFAULT (datetime('now')),
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL,
    output_tokens   INTEGER NOT NULL,
    purpose         TEXT NOT NULL DEFAULT 'story'
);

-- ---------------------------------------------------------------------------
-- note_components  (links sentences/chengyu to their component vocabulary)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS note_components (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
    position    INTEGER NOT NULL,
    UNIQUE(note_id, word_id)
);

-- ---------------------------------------------------------------------------
-- structures  (future)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS structures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern     TEXT NOT NULL,
    description TEXT,
    example_zh  TEXT,
    example_en  TEXT
);
