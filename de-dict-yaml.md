---
name: de-dict-yaml
description: >
  Combined Chinese dictionary + automatic YAML logging skill for a German-speaking Chinese learner.
  Use this skill whenever the user inputs a German word, German sentence, or Chinese word and wants
  a dictionary-style breakdown WITH automatic saving to a YAML vocabulary file. Also triggers when
  the user says "übersetze", "was heißt", "auf Chinesisch", "add to YAML", or gives any word/phrase
  that should be translated AND saved. This skill combines the dictionary skill output format with
  the vocab-to-yaml schema and the following session rules: always show the full analysis in chat
  first, then silently append to the YAML file. For ambiguous German words with multiple Chinese
  translations, show a 💡 Kontextabhängig block first and let the user pick before proceeding.
  Always use this skill in preference to the standalone dictionary or vocab-to-yaml skills when
  both translation AND saving are needed.
---

# De-Dict-YAML Skill

This skill combines two things into one seamless workflow:
1. **Dictionary output** — full Chinese analysis in chat (DeepSeek-style)
2. **Auto-YAML logging** — silently append every entry to the session YAML file

---

## Session Rules (always follow these)

1. **Chat first, YAML second** — Always show the full dictionary analysis in the chat. Then silently append to the YAML file. Never ask "Soll ich speichern?" — just save it.

2. **Ambiguous words → Kontextauswahl first** — If a German word has multiple meaningfully different Chinese translations (different connotation, register, or usage), show the 💡 block and use `ask_user_input_v0` to let the user pick ONE before proceeding with the full analysis.

3. **Unambiguous words → full analysis immediately** — If the word or sentence has one clear Chinese equivalent in context, go straight to the full analysis.

4. **Sentences** — Treat German sentences as translation requests. Produce a `type: sentence` YAML entry with `source_de` and `grammar_de`.

5. **No confirmation prompts** — Never ask "Möchtest du das speichern?" or "Soll ich hinzufügen?". Just do it.

---

## YAML File Management

- The session YAML file lives at: `/mnt/user-data/outputs/vocab_MM_DD.yaml` (date = today)
- If the file already exists this session, **append** to it. Never overwrite.
- After appending, call `present_files` to give the user access to the updated file.
- Check for duplicates by `simplified` field before adding.

---

## Dictionary Output Format (in chat)

### Header block

```
英文：[English translation]
简体中文：[Simplified]
繁体中文：[Traditional]
拼音：[Pinyin with tone marks]
HSK等级：[HSK level or "超纲"]
```

Add a `> 💡` tip block when:
- There are important usage notes (formal vs colloquial variants)
- The German word maps to multiple Chinese words with different connotations
- There's a structural difference between German and Chinese (e.g. word order reversal in sentences)

### Example sentences (minimum 4, ideally 5)

```
**Beispielsätze mit „[word]":**

1. [Chinese]
   ([Pinyin])
   [German translation]
```

### Grammar block (sentences only)

```
**语法 (Grammatik):**
[German explanation of sentence structure, key patterns, particles, aspect markers]
```

### Character analysis

Title: `### 汉字解析 (Character Analysis):`

For **each character**:

**HSK 1–2**: One line only:
```
#### N. 我 (wǒ) – *HSK 1, daher keine detaillierte Analyse*
```

**HSK 3+**: Full block:
```
#### N. 换 / 換 (huàn)
- **Meaning in context**: ...
- **Other meanings**: ...
- **Other examples**: (compounds with pinyin + German)
- **Etymology / Historical development**:
  - Radical + phonetic component
  - Oracle bone / seal script form if relevant
  - Simplified vs. traditional differences
  - How meaning evolved

  **Example sentence illustrating the original meaning:**
  [Chinese] / ([Pinyin]) / [German]
```

---

## YAML Schema

Append entries using the schema below. Field names, indentation, and block scalar style (`|`) must be consistent.

### Vocabulary entry

```yaml
  - type: vocabulary
    date: "MM/DD"
    source_de: [German input word]        # always include for German-origin entries
    simplified: 一流
    traditional: 一流                      # omit if identical to simplified
    pinyin: yī liú
    english: first-class / top-notch
    hsk: "5-6"                            # always a string
    note: |                               # optional, for usage notes
      ...
    examples:
      - zh: ...
        pinyin: ...
        de: ...
    characters:
      - char: 一
        pinyin: yī
        hsk: 1
        detailed_analysis: false
      - char: 流
        pinyin: liú
        hsk: "3-4"
        detailed_analysis: true
        meaning_in_context: ...
        other_meanings:
          - "meaning (example 词 pīnyīn – translation)"
        compounds:
          - simplified: 流行
            pinyin: liúxíng
            meaning: popular, in vogue
        etymology: |
          Prose etymology — no bullet points. Always mention: radical, phonetic
          component, oracle bone/seal script if relevant, simplified vs traditional
          differences, and how the original meaning extended to modern usage.
        etymology_example:
          zh: ...
          pinyin: ...
          de: ...
```

### Sentence entry

```yaml
  - type: sentence
    date: "MM/DD"
    source_de: [Original German sentence]
    simplified: ...
    traditional: ...
    pinyin: ...
    english: ...
    hsk: "3-4"
    grammar_de: |
      German explanation of grammar and structure.
    examples:
      - zh: ...
        pinyin: ...
        de: ...
    characters:
      [same character block as vocabulary]
```

---

## Key Field Rules

| Field | Rule |
|---|---|
| `date` | Today's date as `"MM/DD"` string |
| `source_de` | Always include when input was German |
| `hsk` | Always a string: `"1"`, `"3-4"`, `"5-6"`, `"超纲"` |
| `detailed_analysis` | `false` for HSK 1–2, `true` for HSK 3+ |
| `traditional` | Omit from character block if identical to simplified |
| `etymology` | Always `|` block scalar, prose only — no bullet points inside |
| `note` | Add for important usage/register notes |
| `grammar_de` | Sentences only, always `|` block scalar, always in German |

---

## Language Rules

- **Etymology, meanings, compounds**: in **English**
- **Grammar block** (sentences): in **German**
- **Example sentence translations**: in **German**
- **Usage tips** (💡 blocks): in **German**

---

## Ambiguity Flow

When a German word is ambiguous:

1. Show the 💡 Kontextabhängig block in chat with bullet points for each variant
2. Use `ask_user_input_v0` with `single_select` options (one per variant)
3. Wait for the user's choice
4. Then produce the full dictionary analysis + YAML entry for the chosen word only

Example trigger: "defensiv", "abschreiben", "Aufregung", "wieder" — words where context
changes the Chinese translation significantly.

---

## Readiness Signal

When this skill loads with no input yet, respond only with:

**准备好了。**
