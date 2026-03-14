# Chinese SRS — Project Briefing

## What this is
A personal Spaced Repetition System for learning Mandarin Chinese, built for one user (Daniel, HSK 4–5 level). It replaces Anki with an AI-powered review experience that generates contextual stories and dialogues using the words due for review each day.

## Tech Stack
- **Backend:** Python + FastAPI
- **Database:** SQLite (local, via `sqlite3` stdlib — no ORM)
- **Frontend:** Single HTML file with vanilla JS (no framework) served by FastAPI
- **AI:** Anthropic API (`claude-sonnet-4-6` for evaluation, `claude-haiku-4-5-20251001` for content generation)
- **TTS:** macOS `say` command with Ting-Ting voice for Chinese audio
- **Language:** All UI labels in English, all content in Mandarin Chinese

## Project Structure
```
chinese-srs/
├── CLAUDE.md              # This file
├── main.py                # FastAPI app entry point
├── database.py            # All DB access functions
├── srs.py                 # SM-2 algorithm
├── importer.py            # Language Reactor CSV importer
├── ai.py                  # Anthropic API calls
├── tts.py                 # macOS TTS wrapper
├── schema.sql             # Database schema
├── static/
│   └── index.html         # Single-page frontend
├── data/
│   └── srs.db             # SQLite database (auto-created)
└── imports/               # Drop Language Reactor CSVs here
    └── lln_csv_items_2026-3-14_1067415.csv
```

## Database Schema

### words
```sql
CREATE TABLE words (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_zh TEXT NOT NULL UNIQUE,        -- Chinese characters e.g. 银子
    pinyin TEXT,                          -- cleaned pinyin e.g. yínzi
    definition TEXT,                      -- English definition
    pos TEXT,                             -- part of speech: Noun, Verb, Adj, Adv...
    hsk_level INTEGER DEFAULT 5,          -- 1-6, used to determine "easy" words
    frequency INTEGER DEFAULT 0,          -- from Language Reactor (higher = more common)
    example_zh TEXT,                      -- example sentence in Chinese
    example_en TEXT,                      -- example sentence in English
    date_added TEXT,                      -- ISO datetime string
    source TEXT DEFAULT 'language_reactor'
);
```

### cards
```sql
CREATE TABLE cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    word_id INTEGER NOT NULL REFERENCES words(id),
    category TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    state TEXT DEFAULT 'new' CHECK(state IN ('new', 'learning', 'review')),
    due_date TEXT DEFAULT (date('now')),  -- ISO date string YYYY-MM-DD
    interval INTEGER DEFAULT 1,           -- days until next review
    ease REAL DEFAULT 2.5,               -- SM-2 ease factor
    repetitions INTEGER DEFAULT 0,
    UNIQUE(word_id, category)
);
```

### review_log
```sql
CREATE TABLE review_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES cards(id),
    reviewed_at TEXT DEFAULT (datetime('now')),
    rating INTEGER NOT NULL CHECK(rating IN (1,2,3,4)), -- 1=Again 2=Hard 3=Good 4=Easy
    user_response TEXT,                   -- what the user typed (for creating category)
    ai_score INTEGER                      -- 0-100 score from AI evaluation
);
```

### daily_content
```sql
CREATE TABLE daily_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,                   -- YYYY-MM-DD
    category TEXT NOT NULL CHECK(category IN ('listening', 'reading', 'creating')),
    word_ids TEXT NOT NULL,               -- JSON array of word IDs included
    content_zh TEXT NOT NULL,             -- generated Chinese text
    content_en TEXT NOT NULL,             -- English translation
    sentences_zh TEXT NOT NULL,           -- JSON array of Chinese sentences
    sentences_en TEXT NOT NULL,           -- JSON array of English sentences
    UNIQUE(date, category)
);
```

### structures (future use — scaffold now)
```sql
CREATE TABLE structures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,                -- e.g. 虽然...但是...
    description TEXT,                     -- English explanation
    example_zh TEXT,
    example_en TEXT
);
```

## Core Logic

### SM-2 Algorithm (srs.py)
Implement standard SM-2:
- Ratings: 1=Again, 2=Hard, 3=Good, 4=Easy
- On rating < 3: reset repetitions to 0, interval to 1
- On rating >= 3: calculate next interval using ease factor
- Ease factor adjusts based on rating (never below 1.3)
- Due date = today + interval

### Card Sibling Rule
Words have 3 cards (listening, reading, creating). Only ONE card per word can appear on a given day — siblings are suppressed. When a card is reviewed, push sibling due dates forward by the same interval.

### New Word Flow
New words start with only `listening` and `reading` cards active. The `creating` card is locked until the word has been reviewed at least once in both listening AND reading. Track this with `repetitions` on each card.

Words Daniel already knows well (high frequency + old date_added) should start as `review` state, not `new`.

### "Easy" Words Definition
For story generation: words with `hsk_level <= 3` are considered easy (Daniel knows them without thinking). Since we don't have HSK levels in the import, use `frequency >= 200` as a proxy for "easy/known" words initially.

## Language Reactor CSV Import (importer.py)

### File format
Tab-separated, no header row. Column mapping:
- `[0]` — identifier string like `WORD|银子|zh-CN` (parse word from here as backup)
- `[2]` — example sentence in Chinese (word is **bolded** with asterisks)
- `[3]` — example sentence in English
- `[4]` — word in Chinese characters ← primary field
- `[6]` — part of speech (Noun, Verb, Adj, Adv, Part, Aux, Num, Pron, Det, Adp, Cconj)
- `[8]` — English definition
- `[12]` — pinyin (contains HTML entities like `&#8239;` that must be cleaned)
- `[14]` — frequency score (integer, higher = more common in Chinese)
- `[17]` — date added (format: `2026-03-11 11:41`)

### Pinyin cleaning
Replace `&#8239;` (narrow no-break space) with regular space. Strip `* *` markers.

### Import behavior
- Skip rows where `word_zh` is empty
- Skip duplicates (use `INSERT OR IGNORE`)
- After importing words, create 3 cards per word (listening, reading, creating)
- Set creating card state to 'locked' initially (add a 'locked' state to cards)
- Words with frequency >= 200 AND date_added older than 7 days → set cards to 'review' state with interval=7 (Daniel likely already knows these well)

## AI Content Generation (ai.py)

### Story generation prompt (Haiku — cheap, runs once/day)
```
You are generating a Mandarin Chinese reading/listening text for a language learner at HSK 4-5 level.

RULES:
1. Use ONLY the vocabulary from the "easy words" list plus the "target words" for this session
2. Maximum 2 target words per sentence
3. Keep sentences short and clear (under 20 characters each)
4. The text should be a coherent short story or dialogue (8-12 sentences)
5. Return ONLY valid JSON in this exact format:
{
  "sentences_zh": ["sentence1", "sentence2", ...],
  "sentences_en": ["translation1", "translation2", ...]
}
6. No markdown, no explanation, just the JSON object
```

### Creating evaluation prompt (Sonnet — smarter, per sentence)
```
You are evaluating a Mandarin Chinese translation by a learner.

Original English: {en_sentence}
Correct Chinese: {zh_sentence}  
Target words that must appear: {target_words}
Learner's answer: {user_answer}

Evaluate and return ONLY valid JSON:
{
  "word_correct": true/false,   // did target word(s) appear correctly?
  "grammar_correct": true/false, // is the sentence grammatically valid?
  "score": 0-100,
  "feedback": "brief feedback in English, max 1 sentence"
}
```

## TTS (tts.py)
Use macOS `say` command:
```python
import subprocess
def speak(text: str, rate: int = 180):
    subprocess.run(['say', '-v', 'Ting-Ting', '-r', str(rate), text])
```
Expose a FastAPI endpoint that triggers this so the frontend can request audio playback.

## Review Flow (per category)

### Listening
1. Load all cards due today with category='listening'
2. Generate (or load cached) daily story containing these words
3. Present sentence by sentence — user hears audio ONLY first
4. User clicks "Reveal" → sees Chinese + English text
5. User rates 1-4
6. SM-2 updates card

### Reading  
1. Same as listening but no audio — user sees Chinese text only
2. User clicks "Reveal" → sees English translation
3. User rates 1-4
4. SM-2 updates card

### Creating
1. Load all cards due today with category='creating'
2. Generate (or load cached) daily creating dialogue
3. Present English sentence → user types Chinese translation
4. Submit → AI evaluates → show score + feedback
5. Word rating based on `word_correct`, grammar feedback shown separately
6. If word_correct=false: card stays due, reschedule for later today
7. If grammar wrong but word right: note it (sentence structure feature, future)

## API Endpoints (main.py)

```
GET  /api/today          → { listening: [...cards], reading: [...cards], creating: [...cards] }
GET  /api/content/{date}/{category} → daily_content (generate if not cached)
POST /api/review         → { card_id, rating, user_response? } → updated card
POST /api/speak          → { text } → triggers TTS, returns 200
POST /api/import         → trigger CSV import from imports/ folder
GET  /api/stats          → { total_words, due_today, streak, ... }
```

## Rules & Conventions
- All DB access goes through `database.py` — no raw SQL in other files
- Keep `ai.py` clean — one function per prompt type
- No external dependencies beyond: `fastapi`, `uvicorn`, `anthropic`
- Frontend is a single `index.html` — no build step, no npm
- Never store the API key in code — read from environment variable `ANTHROPIC_API_KEY`
- Always handle the case where AI returns malformed JSON (try/except + fallback)

## Milestones
- **M1** ✅ Plan: DB schema, SM-2, CSV importer, CLI to inspect due cards
- **M2** — Listening module (story generation + TTS + review loop)  
- **M3** — Reading module (same story, visual only)
- **M4** — Creating module (translate sentences + AI evaluation)
- **M5** — Sentence structures as first-class entity
- **M6** — Full UI + deck management + stats

## Start with M1
1. Create `schema.sql` and `database.py`
2. Implement SM-2 in `srs.py`
3. Build `importer.py` for the Language Reactor CSV format
4. Create a simple CLI command `python main.py import` that imports the CSV and prints a summary
5. Create `python main.py status` that shows today's due cards per category
