# YAML 导入格式说明

> **这是 YAML 词汇文件的唯一事实来源。** AI 生成工具（`de-dict-yaml` skill）和手动编写均应遵循此格式。

---

## 目录

1. [通用字段](#通用字段)
2. [类型：`word`（词汇）](#类型-word词汇)
3. [类型：`sentence`（句子）](#类型-sentence句子)
4. [类型：`chengyu` / `expression`（成语 / 惯用表达）](#类型-chengyu--expression成语--惯用表达)
5. [类型：`grammar`（语法点）](#类型-grammar语法点-仅展示不导入)
6. [嵌套结构：`word_analyses`](#嵌套结构-word_analyses)
7. [向后兼容性](#向后兼容性)

---

## 通用字段

所有类型都必须包含以下字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `type` | ✅ | 见下方类型说明 |
| `simplified` | ✅ | 简体中文（作为唯一标识符） |
| `traditional` | — | 繁体中文（如与简体相同可省略） |
| `pinyin` | ✅ | 拼音（带声调） |
| `english` | ✅ | 英文释义 |
| `hsk` | — | HSK 等级，只能填写 `"1"` 到 `"6"` 之一 |
| `date` | — | 添加日期，格式 `"MM/DD"` |

---

## 类型：`word`（词汇）

> 旧格式使用 `type: vocabulary`，两者均被接受。

```yaml
- type: word
  date: "03/26"
  simplified: 绝望
  traditional: 絕望
  pinyin: juéwàng
  english: desperate / hopeless
  hsk: "5"
  register: spoken       # 可选：spoken | written | both
  note: |                # 可选：使用说明或备注

  synonyms:              # 近义词（可选）
    - word: 拼命
      pinyin: pīnmìng
      meaning: verzweifelt, mit letzter Kraft

  antonyms:              # 反义词（可选）
    - word: 希望
      pinyin: xīwàng
      meaning: Hoffnung

  measure_word:          # 量词（可选，仅名词适用）
    - simplified: 种
      pinyin: zhǒng
      meaning: kind, type

  examples:              # 例句（建议 2–3 个）
    - zh: 他对未来感到绝望。
      pinyin: Tā duì wèilái gǎndào juéwàng.
      de: Er ist verzweifelt über die Zukunft.

  characters:            # 汉字分析（可选）
    - char: 绝
      traditional: 絕
      pinyin: jué
      hsk: "5"
      detailed_analysis: true    # false = 只存储基本信息
      meaning_in_context: hoffnungslos
      other_meanings:
        - "断绝，切断"
        - "极，非常"
      compounds:
        - simplified: 绝对
          pinyin: juéduì
          meaning: absolut
      etymology: |
        Das traditionelle Zeichen 絕 ...
```

### register 字段值

| 值 | 含义 |
|----|------|
| `spoken` | 口语（日常对话） |
| `written` | 书面语（正式文章） |
| `both` | 口语和书面语均适用 |
| _(空)_ | 不指定 |

### Recommended Register Values

| Value | Meaning | Example |
|------|------|------|
| `spoken_colloquial` | 口语，umgangssprachlich，Alltag | 啥, 搞定, 靠谱 |
| `spoken_neutral` | 中性口语，neutral im Alltag | 吃, 去, 好 |
| `neutral` | 通用，sowohl muendlich als auch schriftlich | 但是, 因为, 所以 |
| `formal_written` | 书面语，formelle Schriftsprache | 所, 其, 予以, 鉴于 |
| `literary` | 文言，klassisch/literarisch | 之, 者, 亦, 乃 |
| `slang` | 俚语，Jugendsprache，Slang | 躺平, 摆烂, 社死 |

---

## 类型：`sentence`（句子）

```yaml
- type: sentence
  date: "03/26"
  source_de: Wir verlernen, wie man selbst Essen macht.
  simplified: 我们在忘记如何自己做饭。
  traditional: 我們在忘記如何自己做飯。
  pinyin: Wǒmen zài wàngjì rúhé zìjǐ zuòfàn.
  english: We are forgetting how to cook for ourselves.
  hsk: "4"
  explanations: |        # 可选：语法或翻译说明（自由文本）
    ...

  grammar_structures:    # 语法结构（可选，存入 entry_grammar_structures 表）
    - structure: 忘记如何 + 动词
      explanation: "忘记"表示遗忘，"如何"表示方式
      example: 忘记如何自己做饭

  similar_sentences:     # 类似句子（可选，存为 example_type=similar）
    - zh: 年轻人正在忘记如何自己做饭。
      pinyin: Niánqīng rén zhèngzài wàngjì ...
      de: Junge Menschen vergessen, wie man kocht.

  word_analyses:         # 词语分析（见下方说明）
    - type: word
      simplified: 忘记
      pinyin: wàngjì
      english: to forget
      characters:
        - char: 忘
          ...
```

---

## 类型：`chengyu` / `expression`（成语 / 惯用表达）

成语和惯用表达与 `word` 类型结构相同，但：
- 无需 `synonyms` / `antonyms`（一般不设）
- 需要 `word_analyses`（解释各组成词语）

```yaml
- type: chengyu
  simplified: 以次充好
  traditional: 以次充好
  pinyin: yǐ cì chōng hǎo
  english: to pass off inferior goods as high-quality ones
  hsk: "6"
  note: |
    ...
  examples:
    - zh: 这家商店以次充好，欺骗消费者。
      ...
  characters:
    - char: 以
      ...
  word_analyses:
    - type: word
      simplified: 以次
      ...
```

---

## 类型：`grammar`（语法点，仅展示不导入）

> ⚠️ `grammar` 类型**不会被导入**到数据库，导入器会静默跳过。它仅作为学习参考文档使用。

```yaml
- type: grammar
  name: 所 (suǒ) – Nominalisierung mit Verb
  level: "5-6"
  structure: "所 + Verb + 的 (+ Nomen)"
  meaning: "das, was ..."
  usage: |
    ...
  examples:
    - zh: 我所知道的
      pinyin: wǒ suǒ zhīdào de
      de: Das, was ich weiß
  common_patterns:
    - pattern: 所 + V + 的
      meaning: das, was V
      example: 所需要的
```

---

## 嵌套结构：`word_analyses`

`word_analyses` 用于 `sentence` / `chengyu` / `expression` 类型，解释组成词语。

每个分析条目有两种形式：

### 形式 1：完整词语（`type: word`）

```yaml
word_analyses:
  - type: word
    simplified: 忘记
    traditional: 忘記
    pinyin: wàngjì
    english: to forget
    hsk: "4"
    examples:
      - zh: 我忘记了他的名字。
        pinyin: Wǒ wàngjì le tā de míngzì.
        de: Ich habe seinen Namen vergessen.
    characters:
      - char: 忘
        detailed_analysis: true
        ...
```

### 形式 2：单字（`char_only`）

用于 HSK 1–2 的简单字，不需要详细解释：

```yaml
word_analyses:
  - char_only: 我
    pinyin: wǒ
    hsk: "1"
```

---

## 向后兼容性

| 旧字段/值 | 当前支持 | 说明 |
|-----------|----------|------|
| `type: vocabulary` | ✅ | 等同于 `type: word` |
| `measure_word` | ✅ | 量词列表键名 |
| `grammar_de` | ✅ | 存入 `entries.grammar_notes` |
| `source_de` | ✅ | 存入 `entries.source_sentence` |
| `definition_zh` | ✅ | 存入 `entries.definition_zh` |

---

## 数据库映射

| YAML 字段 | 数据库表 / 列 |
|-----------|--------------|
| `simplified` | `entries.word_zh` |
| `english` | `entries.definition` |
| `register` | `entries.register` |
| `synonyms` / `antonyms` | `entry_relations` |
| `measure_word` | `entry_measure_words` |
| `examples` | `entry_examples` (type=`example`) |
| `similar_sentences` | `entry_examples` (type=`similar`) |
| `grammar_structures` | `entry_grammar_structures` |
| `characters` | `entry_characters` → `characters` → `character_compounds` |
| `word_analyses` | `entry_components` + 递归导入子词语 |
