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
-- entries  (formerly 'words' — deck-agnostic vocabulary entries)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_zh         TEXT NOT NULL UNIQUE,
    pinyin          TEXT,
    definition      TEXT,           -- English definition
    pos             TEXT,           -- part of speech
    hsk_level       INTEGER,        -- 1-6, NULL for 超纲
    traditional     TEXT,
    definition_zh   TEXT,
    date_added      TEXT NOT NULL DEFAULT (datetime('now')),
    date_yaml       TEXT,           -- date string from YAML file, e.g. "03/27"
    source          TEXT NOT NULL DEFAULT 'kouyu',
    notes           TEXT,           -- usage notes / explanations from YAML `note` field
    source_sentence TEXT,           -- original source-language sentence (e.g. German) for sentence notes
    grammar_notes   TEXT,           -- grammar explanation (e.g. grammar_de from YAML)
    definition_de   TEXT,           -- German translation / definition
    note_type       TEXT NOT NULL DEFAULT 'vocabulary',
                        -- vocabulary | sentence | chengyu | expression | grammar
    register        TEXT CHECK(register IN ('spoken', 'written', 'both', 'spoken_colloquial', 'spoken_neutral', 'neutral', 'formal_written', 'literary'))
                        -- language register: spoken=口语, written=书面语, both=通用, spoken_colloquial=口语俚语, spoken_neutral=中性口语, neutral=通用, formal_written=正式书面语, literary=文学语体
);

-- ---------------------------------------------------------------------------
-- entry_measure_words  (量词 — classifiers/measure words for a vocabulary entry)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_measure_words (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    measure_zh  TEXT NOT NULL,      -- simplified Chinese, e.g. 种
    pinyin      TEXT,               -- e.g. zhǒng
    meaning     TEXT,               -- English gloss, e.g. "kind, type"
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(word_id, measure_zh)
);

-- ---------------------------------------------------------------------------
-- entry_relations  (synonyms + antonyms — joint table)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_relations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id         INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    related_zh      TEXT NOT NULL,      -- simplified Chinese of the related word
    related_pinyin  TEXT,
    related_de      TEXT,               -- German gloss
    relation_type   TEXT NOT NULL CHECK(relation_type IN ('synonym', 'antonym')),
    UNIQUE(word_id, related_zh, relation_type)
);

-- ---------------------------------------------------------------------------
-- entry_examples
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_examples (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id         INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    example_zh      TEXT NOT NULL,
    example_pinyin  TEXT,
    example_en      TEXT,           -- English translation of the example
    example_de      TEXT,
    position        INTEGER NOT NULL,
    example_type    TEXT NOT NULL DEFAULT 'example'
                        CHECK(example_type IN ('example', 'similar'))
                        -- 'example': normal usage example; 'similar': similar sentence (sentence type)
    -- Note: deduplication enforced in application layer (INSERT OR IGNORE check on example_zh)
);

-- ---------------------------------------------------------------------------
-- entry_grammar_structures  (grammar patterns within sentence entries)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_grammar_structures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    structure   TEXT NOT NULL,      -- e.g. "忘记如何 + 动词"
    explanation TEXT,               -- prose explanation
    example_zh  TEXT,               -- short example phrase
    position    INTEGER NOT NULL DEFAULT 0
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
    compounds       TEXT    -- DEPRECATED: use character_compounds table; kept for migration only
);

-- ---------------------------------------------------------------------------
-- character_compounds  (normalised compound rows linked to a character)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS character_compounds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    char_id     INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    compound_zh TEXT NOT NULL,      -- simplified Chinese compound, e.g. 绝望
    pinyin      TEXT,
    meaning     TEXT,               -- English/German gloss
    position    INTEGER NOT NULL DEFAULT 0,
    UNIQUE(char_id, compound_zh)
);

-- ---------------------------------------------------------------------------
-- entry_characters  (junction table — formerly word_characters)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_characters (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id             INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    char_id             INTEGER NOT NULL REFERENCES characters(id),
    position            INTEGER NOT NULL,
    meaning_in_context  TEXT,
    UNIQUE(word_id, char_id)
);

-- ---------------------------------------------------------------------------
-- cards  (owns deck_id — one card per entry per category, globally unique)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
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
-- story_sentences  (formerly 'sentences')
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS story_sentences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id    INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
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
-- entry_components  (formerly note_components — links sentences/chengyu to their component vocabulary)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entry_components (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
    word_id     INTEGER NOT NULL REFERENCES entries(id) ON DELETE CASCADE,
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

-- ---------------------------------------------------------------------------
-- grammar_points  (type: grammar — reference only, no SRS cards)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grammar_points (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL UNIQUE,   -- display name, e.g. "所 (suǒ) – Nominalisierung"
    level           TEXT,                   -- e.g. "5-6"
    structure       TEXT,                   -- e.g. "所 + Verb + 的 (+ Nomen)"
    meaning         TEXT,                   -- short gloss
    usage           TEXT,                   -- long prose explanation
    cultural_note   TEXT,
    date_added      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- grammar_examples
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grammar_examples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grammar_id  INTEGER NOT NULL REFERENCES grammar_points(id) ON DELETE CASCADE,
    example_zh  TEXT NOT NULL,
    pinyin      TEXT,
    example_de  TEXT,
    structure   TEXT,                       -- structural annotation, e.g. "我 + 所 + 知道 + 的"
    position    INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- grammar_patterns  (common_patterns in YAML)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grammar_patterns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grammar_id  INTEGER NOT NULL REFERENCES grammar_points(id) ON DELETE CASCADE,
    pattern     TEXT NOT NULL,              -- e.g. "所 + V + 的"
    meaning     TEXT,
    example     TEXT,
    position    INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- grammar_comparisons  (comparisons in YAML)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grammar_comparisons (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grammar_id  INTEGER NOT NULL REFERENCES grammar_points(id) ON DELETE CASCADE,
    title       TEXT,
    explanation TEXT,
    position    INTEGER NOT NULL DEFAULT 0
);

-- ---------------------------------------------------------------------------
-- grammar_expressions  (fixed_expressions in YAML)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS grammar_expressions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    grammar_id  INTEGER NOT NULL REFERENCES grammar_points(id) ON DELETE CASCADE,
    expression  TEXT NOT NULL,
    meaning     TEXT,
    position    INTEGER NOT NULL DEFAULT 0
);
