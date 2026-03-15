# YAML Import Format

Files must be placed in `imports/Kouyu/` and end in `.yaml`.
Run `python main.py import` (or `POST /api/import`) to import all files in that directory.
Duplicate words (matched by simplified Chinese) are skipped silently.

---

## Top-level structure

```yaml
entries:
  - type: vocabulary
    # ... fields
  - type: vocabulary
    # ...
```

Only entries with `type: vocabulary` are imported. Any other type is ignored.

---

## Vocabulary entry fields

### Required

| Field | Type | Description |
|-------|------|-------------|
| `simplified` | string | The word in simplified Chinese. Used as the unique key — duplicates are skipped. |

### Recommended

| Field | Type | Description |
|-------|------|-------------|
| `pinyin` | string | Pinyin with tone marks (e.g. `yī mǎn wéi huàn`) |
| `english` | string | English translation / definition |
| `pos` | string | Part of speech (e.g. `noun`, `verb`, `idiom (成语-style phrase)`) |

### Optional

| Field | Type | Description |
|-------|------|-------------|
| `traditional` | string | Traditional character form |
| `definition_zh` | string | Chinese-language definition |
| `hsk` | string | HSK level: `"1"`–`"6"`, or `"超纲"` (off-syllabus → stored as null) |

---

## examples (list, optional)

Up to any number of example sentences. Each item:

```yaml
examples:
  - zh: 她的衣柜里衣满为患，找件衣服要半小时。
    pinyin: Tā de yīguì lǐ yī mǎn wéi huàn, zhǎo jiàn yīfu yào bàn xiǎoshí.
    de: Ihr Kleiderschrank ist so überfüllt, dass sie eine halbe Stunde braucht.
```

| Field | Required | Description |
|-------|----------|-------------|
| `zh` | yes | Example sentence in Chinese |
| `pinyin` | no | Pinyin transcription |
| `de` | no | Translation (currently German, but any language works — it's just displayed as-is) |

---

## characters (list, optional)

One entry per character in the word, in order. Each character can be minimal or fully detailed.

### Minimal character entry

```yaml
characters:
  - char: 衣
    pinyin: yī
    hsk: "2"
    detailed_analysis: false
```

| Field | Required | Description |
|-------|----------|-------------|
| `char` | yes | The single character |
| `pinyin` | no | Pinyin for this character |
| `hsk` | no | HSK level: `"1"`–`"6"`, or `"超纲"` |
| `detailed_analysis` | no | `false` (default) — skips etymology, meanings, compounds |

### Full character entry (`detailed_analysis: true`)

```yaml
characters:
  - char: 满
    traditional: 滿
    pinyin: mǎn
    hsk: "3"
    detailed_analysis: true
    meaning_in_context: full, filled to capacity
    other_meanings:
      - satisfied, contented (满足 mǎnzú)
      - completely, fully (满意 mǎnyì)
    compounds:
      - simplified: 满足
        pinyin: mǎnzú
        meaning: satisfied, to satisfy
      - simplified: 满意
        pinyin: mǎnyì
        meaning: satisfied, content
    etymology: |
      满 consists of the water radical 氵 on the left ...
```

| Field | Required | Description |
|-------|----------|-------------|
| `char` | yes | The single character |
| `traditional` | no | Traditional form |
| `pinyin` | no | Pinyin |
| `hsk` | no | HSK level: `"1"`–`"6"`, or `"超纲"` |
| `detailed_analysis` | yes | Must be `true` to enable the fields below |
| `meaning_in_context` | no | How this character contributes to the word's meaning |
| `other_meanings` | no | List of strings — other common meanings of the character |
| `compounds` | no | List of common compound words using this character (see below) |
| `etymology` | no | Multi-line string explaining the character's origin |

Each item in `compounds`:

```yaml
compounds:
  - simplified: 满足
    pinyin: mǎnzú
    meaning: satisfied, to satisfy
```

---

## Minimal working example

```yaml
entries:
  - type: vocabulary
    simplified: 学习
    pinyin: xuéxí
    english: to study, to learn
    pos: verb
    hsk: "1"
```

## Full example

```yaml
entries:
  - type: vocabulary
    simplified: 衣满为患
    traditional: 衣滿為患
    pinyin: yī mǎn wéi huàn
    english: to have too many clothes; a closet crisis
    hsk: 超纲
    pos: idiom (成语-style phrase)
    definition_zh: 因衣服多而造成麻烦。
    examples:
      - zh: 她的衣柜里衣满为患，找件衣服要半小时。
        pinyin: Tā de yīguì lǐ yī mǎn wéi huàn, zhǎo jiàn yīfu yào bàn xiǎoshí.
        de: Ihr Kleiderschrank ist so überfüllt, dass sie eine halbe Stunde braucht.
    characters:
      - char: 衣
        pinyin: yī
        hsk: "2"
        detailed_analysis: false
      - char: 满
        traditional: 滿
        pinyin: mǎn
        hsk: "3"
        detailed_analysis: true
        meaning_in_context: full, filled to capacity
        other_meanings:
          - satisfied, contented (满足 mǎnzú)
          - completely, fully (满意 mǎnyì)
        compounds:
          - simplified: 满足
            pinyin: mǎnzú
            meaning: satisfied, to satisfy
        etymology: |
          满 consists of the water radical 氵 on the left and 㒼 as the phonetic
          component. Original meaning: water filling a vessel to the brim.
```
