# Step 09 — Browse: AI Enrich Endpoint + Character Browse

**Branch:** `feat/browse-enrich`
**Depends on:** Step 01 (DB), Step 02 (AI multi-provider)
**Blocks:** Step 15 (Frontend: HSK badge)

---

## What to Read First

1. `routes/browse.py` — full file
2. `ai.py` — `enrich_word()` function (updated in Step 02)
3. `database.py` — `get_word_full()`, `get_word_characters()`, `update_word()`,
   `update_character()`, `get_characters_for_browse()`, `get_character_full()`
4. `recovery/2026-03-24_deepseek-and-story-modal-fix.md` — `enrich_word` full code
5. `recovery/2026-03-24_trash-browse-redesign.md` — character browse

---

## Goal

1. Add `POST /api/word/{word_id}/ai-enrich` endpoint
2. Add `GET /api/characters` endpoint for the Browse > Hanzi tab
3. Add `GET /api/characters/{char}` endpoint for character detail

---

## `POST /api/word/{word_id}/ai-enrich`

Uses Haiku AI to fill missing HSK level and character data (etymology, other_meanings).
**Never overwrites existing data** — only fills null/empty fields.

```python
@router.post("/api/word/{word_id}/ai-enrich")
def ai_enrich_word(word_id: int):
    word = database.get_word(word_id)
    if not word:
        raise HTTPException(status_code=404, detail="Word not found")
    characters = database.get_word_characters(word_id)

    result = ai.enrich_word(word, characters)

    # Update word HSK level only if currently missing
    if result.get("hsk_level") and not word.get("hsk_level"):
        database.update_word(word_id, {"hsk_level": result["hsk_level"]})

    # Update character fields — only write fields that were empty
    char_map = {c["char"]: c for c in characters}
    for char_data in result.get("characters", []):
        ch = char_data.get("char")
        if not ch or ch not in char_map:
            continue
        existing = char_map[ch]
        updates = {}
        if char_data.get("etymology") and not existing.get("etymology"):
            updates["etymology"] = char_data["etymology"]
        if char_data.get("other_meanings") and not existing.get("other_meanings"):
            meanings = char_data["other_meanings"]
            if isinstance(meanings, list):
                import json as _json
                updates["other_meanings"] = _json.dumps(meanings, ensure_ascii=False)
        if updates:
            database.update_character(existing["char_id"], updates)

    return database.get_word_full(word_id)
```

### `ai.enrich_word()` — exact prompt from recovery docs

```python
def enrich_word(word: dict, characters: list[dict]) -> dict:
    """
    Determine HSK level for a word and fill missing character data.
    Only requests data for fields that are currently empty.
    Returns: {hsk_level: int|None, characters: [{char, etymology, other_meanings}]}
    """
    # Build char_section only for characters needing data
    chars_needing_data = []
    for c in characters:
        needs = []
        if not c.get("etymology"):
            needs.append("etymology")
        if not c.get("other_meanings"):
            needs.append("other_meanings (array of short English meanings)")
        if needs:
            chars_needing_data.append(
                f'  - {c["char"]} (pinyin: {c.get("pinyin", "")}) → needs: {", ".join(needs)}'
            )

    char_section = ""
    if chars_needing_data:
        char_section = (
            "\n\nFor each character below, provide only the requested fields:\n"
            + "\n".join(chars_needing_data)
            + '\n\nReturn these under "characters" as an array of objects with keys: '
            '"char", "etymology" (2–4 sentences on origin & components), '
            '"other_meanings" (array of 2–5 short English strings).'
        )
    else:
        char_section = '\n\nNo character data needed — return "characters": [].'

    prompt = f"""You are a Chinese language expert. For the word {word["word_zh"]} \
({word.get("pinyin", "")}) — {word.get("definition", "")}:

1. What is its HSK level (1–6)? Return null if it is not in the standard HSK list.{char_section}

Return ONLY valid JSON, no explanation, no markdown:
{{
  "hsk_level": <integer 1-6 or null>,
  "characters": [
    {{"char": "<char>", "etymology": "<text>", "other_meanings": ["<m1>", "<m2>"]}}
  ]
}}"""

    # Use claude-haiku-4-5-20251001 for this (precise structured task)
    text = _call_api("claude-haiku-4-5-20251001", [{"role": "user", "content": prompt}],
                     max_tokens=800, purpose=f"enrich:{word['word_zh']}")

    json_match = re.search(r'\{.*\}', text, re.DOTALL)
    if json_match:
        text = json_match.group(0)
    try:
        result = json.loads(text)
        return {"hsk_level": result.get("hsk_level"), "characters": result.get("characters", [])}
    except (json.JSONDecodeError, TypeError, KeyError) as e:
        logger.error("enrich_word: JSON parse error: %s", e)
        return {"hsk_level": None, "characters": []}
```

---

## `GET /api/characters` — Character Browse

```python
@router.get("/api/characters")
def list_characters():
    return database.get_characters_for_browse()
    # Returns list of {char, pinyin, other_meanings, etymology, word_count}
    # sorted by pinyin
```

## `GET /api/characters/{char}` — Character Detail

```python
@router.get("/api/characters/{char}")
def get_character(char: str):
    result = database.get_character_full(char)
    if not result:
        raise HTTPException(status_code=404, detail="Character not found")
    return result
    # Returns: {char, pinyin, other_meanings, etymology, words: [...]}
```

---

## `update_character` DB function

Check if `database.update_character(char_id, updates)` exists. If not, add it:

```python
def update_character(char_id: int, updates: dict) -> None:
    allowed = {"etymology", "other_meanings", "pinyin", "meaning_in_context"}
    fields = {k: v for k, v in updates.items() if k in allowed}
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    with _conn() as db:
        db.execute(f"UPDATE characters SET {cols} WHERE id=?", (*fields.values(), char_id))
```

---

## How to Implement

1. `git checkout -b feat/browse-enrich` (after Steps 01 and 02 are merged)
2. Edit `routes/browse.py` — add `ai_enrich_word`, `list_characters`, `get_character`
3. Edit `ai.py` — add/update `enrich_word()` using `_call_api()`
4. Edit `database.py` — add `update_character()` if missing, `get_characters_for_browse()`, `get_character_full()`
5. Test:
   ```bash
   curl -X POST http://localhost:8000/api/word/1/ai-enrich
   curl http://localhost:8000/api/characters
   ```
6. Commit and open PR

---

## Verification Checklist

- [ ] `POST /api/word/{id}/ai-enrich` fills missing HSK level
- [ ] `POST /api/word/{id}/ai-enrich` fills missing character etymology
- [ ] Existing data is never overwritten by enrich
- [ ] `GET /api/characters` returns all characters sorted by pinyin
- [ ] `GET /api/characters/{char}` returns character with containing words
- [ ] API call is logged in `api_cost_log`
