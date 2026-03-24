# YAML Import Format

YAML files can be imported via the **Import** button in the UI. You can also drop files into `imports/` and run `python main.py import`.

The top-level key can be `entries:` or `vocab:`.

---

## Note types

Three types of top-level entries are supported. Each creates **3 review cards** (Listening, Reading, Creating) in the target deck.

| `type` | Description |
|--------|-------------|
| `vocabulary` | A single word or multi-character word |
| `sentence` | A full sentence (periods and long strings allowed) |
| `chengyu` | A four-character idiom (成语) |

Any other type is skipped with a warning.

---

## Deduplication

- A note is a **duplicate** if it already has review cards. It is skipped.
- A note that exists in the database but has **no cards** (was previously only a component word) is **not** a duplicate — cards will be created for it.
- Component words inside `word_analyses` are never cards — they are stored for reference only and linked to the parent note.

---

## Top-level entry fields

All three types share the same core fields.

### Required

| Field | Type | Description |
|-------|------|-------------|
| `type` | string | `vocabulary`, `sentence`, or `chengyu` |
| `simplified` | string | The note in simplified Chinese. Used as the unique key. |

### Recommended

| Field | Type | Description |
|-------|------|-------------|
| `pinyin` | string | Pinyin with tone marks |
| `english` | string | English translation / definition |
| `hsk` | string | HSK level: `"1"`–`"6"`, or `"超纲"` |

### Optional

| Field | Type | Description |
|-------|------|-------------|
| `traditional` | string | Traditional character form |
| `pos` | string | Part of speech (vocabulary only) |
| `definition_zh` | string | Chinese-language definition |
| `source_de` | string | Original German input (from de-dict-yaml skill) |
| `note` | string | Personal usage notes |
| `grammar_de` | string | Grammar explanation in German (sentences only) |
| `literal` | string | Literal translation (chengyu only) |
| `origin` | string | Classical origin of the idiom (chengyu only) |

---

## examples (list, optional)

```yaml
examples:
  - zh: 我喜欢在电脑上编程。
    pinyin: Wǒ xǐhuān zài diànnǎo shàng biānchéng.
    de: Ich programmiere gerne am Computer.
```

| Field | Required | Description |
|-------|----------|-------------|
| `zh` | yes | Example sentence in Chinese |
| `pinyin` | no | Pinyin transcription |
| `de` | no | Translation (displayed as-is) |

---

## characters (list, optional)

For `vocabulary` and `chengyu` entries. One entry per character, in order.

### Minimal

```yaml
characters:
  - char: 程
    pinyin: chéng
    hsk: "4"
    detailed_analysis: false
```

### Full (`detailed_analysis: true`)

```yaml
characters:
  - char: 编
    traditional: 編
    pinyin: biān
    hsk: "5-6"
    detailed_analysis: true
    meaning_in_context: to compile, to write (code)
    other_meanings:
      - "编写 (biānxiě – to write/compile)"
      - "编辑 (biānjí – to edit)"
    compounds:
      - simplified: 编写
        pinyin: biānxiě
        meaning: to write, to compile
    etymology: |
      The traditional 編 uses the silk/thread radical (糸) ...
    etymology_example:
      zh: 她在编一顶帽子。
      pinyin: Tā zài biān yī dǐng màozi.
      de: Sie strickt einen Hut.
```

| Field | Required | Description |
|-------|----------|-------------|
| `char` | yes | The single character |
| `traditional` | no | Traditional form if different from simplified |
| `pinyin` | no | Pinyin for this character |
| `hsk` | no | HSK level |
| `detailed_analysis` | no | `true` enables etymology, meanings, compounds |
| `meaning_in_context` | no | How this character contributes to the word's meaning |
| `other_meanings` | no | List of strings — other common meanings |
| `compounds` | no | List of `{simplified, pinyin, meaning}` compound words |
| `etymology` | no | Multi-line prose (block scalar `\|`) explaining the character's origin |
| `etymology_example` | no | `{zh, pinyin, de}` — an example illustrating the original meaning |

---

## word_analyses (list, optional — sentences and chengyu only)

Lists the component vocabulary words that make up the sentence or idiom. These words are stored in the database and linked to the parent note for display in Browse — **they do not get their own review cards**.

Only items with `type: vocabulary` are processed. Items with `char_only` (no `type`) are annotation-only markers and are skipped.

```yaml
word_analyses:
  - char_only: 我          # annotation only — skipped
    pinyin: wǒ
    hsk: "1"
    detailed_analysis: false
  - type: vocabulary
    simplified: 编程
    traditional: 編程
    pinyin: biānchéng
    english: to program; programming
    hsk: "5-6"
    characters:
      - char: 编
        traditional: 編
        pinyin: biān
        hsk: "5-6"
        detailed_analysis: true
        meaning_in_context: to compile, to write (code)
        etymology: |
          ...
      - char: 程
        pinyin: chéng
        hsk: "4"
        detailed_analysis: true
        meaning_in_context: procedure; process
        etymology: |
          ...
```

### Conflict resolution

If a component word already exists in the database with **different** data (pinyin, definition, or traditional form), the import preview will flag it as a conflict. You can then choose per-word:

- **Keep Existing** — leave the database unchanged (default)
- **Use Incoming** — overwrite with the data from the new YAML

---

## Minimal examples

**Vocabulary:**
```yaml
vocab:
  - type: vocabulary
    simplified: 学习
    pinyin: xuéxí
    english: to study, to learn
    hsk: "1"
```

**Sentence:**
```yaml
vocab:
  - type: sentence
    simplified: 我喜欢在电脑上编程。
    pinyin: Wǒ xǐhuān zài diànnǎo shàng biānchéng.
    english: I love programming on the computer.
    hsk: "3-5"
    word_analyses:
      - type: vocabulary
        simplified: 编程
        pinyin: biānchéng
        english: to program; programming
        hsk: "5-6"
```

**Chengyu:**
```yaml
vocab:
  - type: chengyu
    simplified: 左右为难
    traditional: 左右為難
    pinyin: zuǒ yòu wéi nán
    english: to be in a dilemma
    hsk: "超纲"
    word_analyses:
      - type: vocabulary
        simplified: 为难
        traditional: 為難
        pinyin: wéinán
        english: to be in a difficult position
        hsk: "超纲"
```
