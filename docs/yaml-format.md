# YAML 导入格式说明

> **这是 YAML 词汇文件的唯一事实来源。** AI 生成工具（`de-zh-bot` skill）和手动编写均应遵循此格式。
> `test.yaml` 是经过验证的规范示例——格式有疑问时以它为准。

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
| `german` | ✅ | 德文释义 |
| `hsk` | — | HSK 等级，只能填写 `"1"` 到 `"6"` 之一（带引号的单个数字） |
| `date` | — | 添加日期，格式 `"MM/DD"` |

---

## 类型：`word`（词汇）

> 旧格式使用 `type: vocabulary`，两者均被接受。

```yaml
- type: word
  date: "03/26"
  simplified: 生态
  traditional: 生態             # 与简体相同时省略
  pinyin: shēngtài
  english: ecology / ecosystem
  german: Ökologie / Ökosystem
  hsk: "5"
  register: formal_written      # 可选：见下方 register 值说明
  measure_word:                 # 可选，仅名词适用
    - simplified: 种
      pinyin: zhǒng
      meaning: kind or type (for ecosystems)
    - simplified: 个
      pinyin: gè
      meaning: general classifier in figurative contexts
  note: |                       # 可选：英文使用说明与备注
    A noun meaning "ecology"...

    **Common Expressions:**
    - 生态环境 (shēngtài huánjìng) — ecological environment

  examples:                     # 建议 2–4 个，每个例句含 4 个字段
    - zh: 保护生态环境是我们每个人的责任。
      pinyin: Bǎohù shēngtài huánjìng shì wǒmen měi gè rén de zérèn.
      english: Protecting the ecological environment is the responsibility of every one of us.
      de: Den ökologischen Umwelt zu schützen ist die Verantwortung eines jeden von uns.

  characters:                   # 汉字分析（可选）
    - char: 生
      simplified: 生            # 必须包含，即使与 char 相同
      traditional: 生           # 与简体相同时省略
      pinyin: shēng
      hsk: "1"
      detailed_analysis: true  # HSK 3+ 为 true；HSK 1–2 为 false
      meaning_in_context: life, living
      compounds:
        - simplified: 生命
          pinyin: shēngmìng
          meaning: life
        - simplified: 生活
          pinyin: shēnghuó
          meaning: life, livelihood
      etymology: |
        纯散文，不含列表。说明：部首、声符（表音字）、甲骨文/金文来源（如有）、意义演变。
```

### register 字段值

| 值 | 含义 |
|----|------|
| `spoken_colloquial` | 口语，umgangssprachlich |
| `spoken_neutral` | 中性口语 |
| `neutral` | 通用（口语+书面均适用） |
| `formal_written` | 书面语，正式文章 |
| `literary` | 文言，klassisch/literarisch |
| `slang` | 俚语，Jugendsprache |

---

## 类型：`sentence`（句子）

```yaml
- type: sentence
  date: "03/26"
  source_de: Ich werde dir zur passenden Zeit die Wahrheit sagen.  # 德文输入时包含
  simplified: 在适当的时候，我会告诉你真相。
  traditional: 在適當的時候，我會告訴你真相。
  pinyin: Zài shìdàng de shíhou, wǒ huì gàosu nǐ zhēnxiàng.
  english: I will tell you the truth at the appropriate time.
  hsk: "5"
  explanations: |              # 语法与词汇说明（英文），sentence 类型专用
    这句话使用了时间状语从句...

    - 在适当的时候 (zài shìdàng de shíhou) — at the appropriate time
    - 告诉 (gàosu) — to tell

  grammar_structures:          # 语法结构（可选）
    - structure: 在 + 时间状语 + 主语 + 会 + 动词 + 宾语
      explanation: 在适当的时候 is a time adverbial at sentence start.
      example: 在适当的时候，我会告诉你。

  similar_sentences:           # 类似句子（可选）
    - zh: 在合适的时机，我会告诉你。
      pinyin: Zài héshì de shíjī, wǒ huì gàosu nǐ.
      de: Ich werde es dir beim passenden Anlass sagen.

  word_analyses:               # 句中关键词分析（见下方说明）
    - type: word
      simplified: 适当
      ...
```

---

## 类型：`chengyu` / `expression`（成语 / 惯用表达）

两者格式相同，与 `word` 类型结构一致，另加：
- `synonyms` / `antonyms`（带 `word`、`pinyin`、`meaning`，chengyu 必填，expression 可选）
- `word_analyses`（解释各组成词语）

**类型区分：**
- `chengyu`：经典四字成语，有文言出处（如 同心协力、马到成功）
- `expression`：多词短语、固定搭配、口语表达——不是单个词语，也不是完整句子，也不是四字成语（如 说话的方式、愛上了、感到有責任、我快饿死了）

```yaml
- type: chengyu
  simplified: 同心协力
  traditional: 同心協力
  pinyin: tóng xīn xié lì
  english: to work together with one heart
  hsk: "5"
  register: formal_written
  note: |
    ...
  examples:
    - zh: 只有大家同心协力，才能完成这项艰巨的任务。
      pinyin: Zhǐyǒu dàjiā tóngxīn xiélì, cáinéng wánchéng zhè xiàng jiānjù de rènwu.
      english: Only when everyone works together can we complete this arduous task.
      de: Nur wenn alle gemeinsam an einem Strang ziehen, können wir diese Aufgabe bewältigen.
  synonyms:
    - word: 齐心协力
      pinyin: qíxīn xiélì
      meaning: to work together with one heart
  antonyms:
    - word: 一盘散沙
      pinyin: yīpán sǎnshā
      meaning: a sheet of loose sand (disorganized)
  word_analyses:
    - type: word
      simplified: 同心
      ...
```

---

## 类型：`grammar`（语法点，仅展示不导入）

> ⚠️ `grammar` 类型**不会被导入**到数据库，导入器会静默跳过。

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

### 形式 1：完整词语（`type: word`）

```yaml
word_analyses:
  - type: word
    simplified: 适当
    traditional: 適當
    pinyin: shìdàng
    english: appropriate, suitable
    hsk: "5"
    characters:
      - char: 适
        simplified: 适
        traditional: 適
        pinyin: shì
        hsk: "4"
        detailed_analysis: true
        meaning_in_context: to fit, to suit
        compounds:
          - simplified: 适合
            pinyin: shìhé
            meaning: to suit, to fit
        etymology: |
          Phono-semantic compound. Traditional form 適 consists of radical 辶 (walk)
          and phonetic 啇 (dí). Original meaning is "to go toward," extended to "to fit."
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

## 语言规则

| 字段 | 语言 |
|------|------|
| `note` | **德语** |
| `explanations`（sentence 类型） | **德语** |
| `etymology` | **德语** |
| `meaning_in_context` | **德语** |
| `compounds[].meaning` | **德语** |
| `examples[].english` | 英语 |
| `examples[].de` | 德语 |
| `similar_sentences[].de` | 德语 |
| `synonyms/antonyms[].meaning` | 德语 |
| `measure_word[].meaning` | 德语 |
| `grammar_structures[].explanation` | 德语 |

---

## 关键字段规则

| 字段 | 规则 |
|------|------|
| `hsk` | 始终为带引号的单个数字：`"1"` `"2"` `"3"` `"4"` `"5"` `"6"` |
| `traditional` | 仅在与 `simplified` 不同时包含（词条级和字符块级均适用） |
| 字符块内的 `simplified` | 始终包含，即使与 `char` 相同 |
| `detailed_analysis` | HSK 3+ 为 `true`；HSK 1–2 为 `false` |
| `etymology` | 始终使用 `\|` 块标量，纯散文——内部不含列表——**德语** |
| `examples` | 始终包含全部 4 个字段：`zh`、`pinyin`、`english`、`de` |
| `note` vs `explanations` | `word`/`chengyu`/`expression` 用 `note`；`sentence` 用 `explanations` |

---

## 向后兼容性

| 旧字段/值 | 当前支持 | 说明 |
|-----------|----------|------|
| `type: vocabulary` | ✅ | 等同于 `type: word` |
| `measure_word` | ✅ | 量词列表键名 |
| `explanations` | ✅ | sentence 类型字段，写入 `entries.notes` |
| `source_de` | ✅ | 存入 `entries.source_sentence` |
| `definition_zh` | ✅ | 存入 `entries.definition_zh` |

---

## 数据库映射

| YAML 字段 | 数据库表 / 列 |
|-----------|--------------|
| `simplified` | `entries.word_zh` |
| `english` | `entries.definition` |
| `register` | `entries.register` |
| `note` / `explanations` | `entries.notes` |
| `synonyms` / `antonyms` | `entry_relations` |
| `measure_word` | `entry_measure_words` |
| `examples` | `entry_examples` (type=`example`) |
| `similar_sentences` | `entry_examples` (type=`similar`) |
| `grammar_structures` | `entry_grammar_structures` |
| `characters` | `entry_characters` → `characters` → `character_compounds` |
| `word_analyses` | `entry_components` + 递归导入子词语 |
