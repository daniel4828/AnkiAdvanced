# 中文间隔重复系统 — 项目说明

## 语言指令

用中文回答。

### 第一步 - 用中文重写用户的消息
- 总是从将用户的输入改写为一个干净、正确的中文句子开始
- 如果用户使用英语或混合语言（例如："我如何implement这个feature？"），将其翻译/改写为中文
- 如果用户用中文写了错误，在重写时默默地纠正它们，不要单独解释错误
- 如果用户的中文已经很完美了，就按原样重写

**例子：**
| 用户输入 | 第一步输出 |
|----------|------------|
| `how do I fix this bug?` | 我怎么修复这个错误？ |
| `我如何implement这个feature？` | 我怎么实现这个功能？ |
| `这个function为什么return了None` | 这个函数为什么返回了None？ |
| `我想add一个新的endpoint` | 我想添加一个新的接口。 |
| `数据库的schema是什么` | 数据库的模式是什么？ *(中文已正确，按原样重写)* |

### 第二步 - 绘制分割线
在重写的问题下面，画一条分割线，格式如下：

**纠正的问题**

--------------------------------------------

*(然后在分割线下面开始回答)*

### 第三步 - 回答
在回答中，超过HSK6级的词请这样写：文件（wénjiàn - file）

**例子：**
> ❌ 错误：这个函数返回了一个异步生成器。
> ✅ 正确：这个函数返回了一个异步（yìbù - asynchronous）生成器（shēngchéng qì - generator）。

---

## 项目简介

这是一个供个人使用的间隔重复（jiàngé chóngfù - Spaced Repetition）系统，专为一位用户（Daniel，汉语水平（shuǐpíng - level）HSK 4–5）打造。它用AI驱动的复习体验取代了Anki，每天根据当天到期复习的词汇（cíhuì - vocabulary）生成上下文故事。

## 技术栈（jìshù zhàn - tech stack）

- **后端（hòuduān - backend）：** Python + FastAPI
- **数据库（shùjùkù - database）：** SQLite（本地，使用标准库 `sqlite3`，无ORM）
- **前端（qiánduān - frontend）：** 单个HTML文件 + 原生JS（无框架），由FastAPI提供服务
- **AI：** Anthropic API（`claude-sonnet-4-6` 用于评估，`claude-haiku-4-5-20251001` 用于故事生成）
- **语音合成（yǔyīn héchéng - TTS）：** `edge-tts`，使用语音 `zh-CN-XiaoxiaoNeural`（通过 `afplay` 播放）
- **语言：** 所有界面（jièmiàn - UI）标签使用英文，所有内容使用中文

## 项目结构（jiégòu - structure）

```
├── CLAUDE.md              # 本文件
├── main.py                # CLI入口 + FastAPI应用
├── database.py            # 所有数据库访问（其他文件不写原始SQL）
├── srs.py                 # Anki风格的SM-2调度（diàodù - scheduling）算法
├── importer.py            # 口语YAML导入（dǎorù - import）器
├── ai.py                  # Anthropic API调用
├── tts.py                 # edge-tts封装（fēngzhuāng - wrapper）
├── schema.sql             # 数据库模式（móshì - schema）
├── static/
│   └── index.html         # 单页前端（M2+）
├── data/
│   └── srs.db             # SQLite数据库（自动创建）
└── imports/
    └── Kouyu/             # YAML词汇文件（唯一导入来源）
```

## 数据来源（láiyuán - source）

**唯一来源：** `imports/Kouyu/*.yaml` —— 包含词性（cí xìng - part of speech）、例句（lìjù - example sentences）、词源（cí yuán - etymology）、汉字（hànzì - character）分解的丰富YAML文件。

不导入HSK CSV文件。AI在故事提示词（tíshící - prompt）中被告知"非目标词汇只使用HSK 1–2的词汇"。

## 数据库模式（概述）

```
deck_presets → decks（自引用parent_id，支持嵌套）→ words
                                                      ├── word_examples
                                                      ├── word_characters → characters
                                                      └── cards → review_log

decks → stories → sentences → words（外键）
```

关键设计决策（juécè - design decisions）：
- `cards.due` 是单一TEXT字段：学习/重新学习状态为ISO日期时间，新建/复习状态为ISO日期
- `cards.state`：`new`（新建）| `learning`（学习中）| `review`（复习）| `relearn`（重新学习）| `suspended`（暂停）
- `cards.step_index` 追踪（zhuīzōng - track）学习/重新学习步骤的当前位置
- `cards.lapses` 统计复习失败次数（用于检测难词）
- `stories` 没有唯一约束（yuēshù - constraint）—— 同一（日期、类别、牌组）可有多条记录；最新的 `generated_at` 为当前活跃故事；永不自动删除
- `sentences` 通过位置将故事与词汇关联，每个故事每个词汇只对应一个句子（1:1约束）

## 调度算法 —— Anki SM-2 变体（biàntǐ - variant）

### 状态（zhuàngtài - states）
- `new`（新建）→ `learning`（学习中）（首次复习）
- `learning`（学习中）→ `review`（复习）（所有步骤通过后毕业）
- `review`（复习）→ `relearn`（重新学习）（评为Again = 遗忘）
- `relearn`（重新学习）→ `review`（复习）（重新学习步骤完成）

### 学习步骤（默认：1分钟、10分钟）
- **Again（再来一次）** → step_index=0，due=现在+steps[0]分钟
- **Hard（困难）** → step_index不变；step 0时延迟（yánchí - delay）= step[0]+step[1]的平均值，否则为当前步骤
- **Good（良好）** → 推进步骤；若为最后一步 → 毕业（state=review，interval=graduating_interval）
- **Easy（简单）** → 立即毕业（interval=easy_interval）

### 复习阶段（jiēduàn - phase）
- **Again（再来一次）** → 遗忘：ease-=0.20，interval×0.5，state=relearn
- **Hard（困难）** → ease-=0.15，interval×1.2
- **Good（良好）** → interval×ease
- **Easy（简单）** → ease+=0.15，interval×ease×1.3

ease最低值（zuì dī zhí - floor）：1.3。难词检测（jiǎncè - detection）：遗忘次数 >= leech_threshold 时暂停卡片。

## 队列（duìliè - queue）设计

不使用队列表。`get_due_cards()` 每次调用时从数据库实时状态组装队列。
`get_next_card()` 返回优先级最高的卡片（LIMIT 1）：
  1. 当日内到期（学习/重新学习，due <= 现在）
  2. 复习卡片（due <= 今天）
  3. 新建卡片（不超过每日上限）

`POST /api/review` 返回 `{next_card, counts}` —— 无需额外请求。

## 故事生成（shēngchéng - generation）

- 每个类别（阅读/听力/写作）独立生成自己的故事
- 每个目标词汇（mùbiāo cíhuì - target vocabulary）恰好对应一个句子（1:1按位置映射）
- 使用 `get_due_cards()` 收集所有目标词汇用于AI提示词
- `create_story()` 每次都插入新行 —— 重新生成 = 新增一行，旧故事永久保留
- Haiku提示词要求：连贯叙事（sùshì - narrative），相同人物，每句不超过15个字，背景词汇使用HSK 1–2

## 界面类别顺序（shùnxù - order）

阅读 → 听力 → 写作

## API 接口（jiēkǒu - endpoints）

```
GET  /api/decks                          → 带到期数量的牌组（páizǔ - deck）树
POST /api/decks                          → 创建牌组
PUT  /api/decks/{id}                     → 重命名牌组
GET  /api/decks/{id}/preset              → 获取预设（yùshè - preset）设置
PUT  /api/decks/{id}/preset              → 更新预设设置

GET  /api/today/{deck_id}/{category}     → {card, counts}（最优先卡片 + 进度数量）
GET  /api/story/{deck_id}/{category}     → 今日活跃故事（如无则生成）
POST /api/story/{deck_id}/{category}/regenerate → 从当前队列生成新故事
POST /api/review                         → {card_id, rating, user_response?}
                                           返回 {next_card, counts}
POST /api/speak                          → {text} → 触发语音合成
POST /api/import                         → 触发YAML导入
GET  /api/browse                         → {deck_id?, category?, state?, q?}
GET  /api/stats                          → 全局或按牌组统计（tǒngjì - stats）
```

## 命令行（mìnglìng háng - CLI）

```bash
python main.py import               # 导入所有口语YAML文件
python main.py status               # 显示每个牌组/类别的到期数量
python main.py status --deck Kouyu  # 筛选（shāixuǎn - filter）单个牌组
```

## 规范（guīfàn - conventions）与约束

- 所有数据库访问通过 `database.py` —— 其他文件不写原始SQL
- 保持 `ai.py` 简洁 —— 每种提示词类型对应一个函数
- 除以下库外不引入外部依赖（yīlài - dependencies）：`fastapi`、`uvicorn`、`anthropic`、`edge-tts`、`pyyaml`
- 前端是单一 `index.html` —— 无需构建步骤（gòujiàn bùzhòu - build step），无npm
- API密钥（mìyuè - key）不存储在代码中 —— 从环境变量（huánjìng biànliàng - environment variable）`ANTHROPIC_API_KEY` 读取
- 始终使用 try/except + 回退（huí tuì - fallback）处理AI返回的格式错误JSON

## 里程碑（lǐchéngbēi - milestones）

- **M1** ✅ 数据库模式、SM-2算法、口语YAML导入器、CLI（`import` + `status`）
- **M2** —— 听力模块（故事生成 + 语音合成 + 复习循环 + 前端）
- **M3** —— 阅读模块（复用M2故事，无语音合成）
- **M4** —— 写作模块（自评翻译）
- **M5** —— 完整Anki风格界面（牌组列表、浏览、设置弹窗、统计）
- **M6** —— 精细化（jīngxì huà - polish）（连续打卡、难词标记界面、AnkiConnect导出）