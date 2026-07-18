# 中文间隔重复系统 — 项目说明

> 🔴 **【每条消息必须做的第一件事】** 在写任何内容之前，先把 Daniel 的消息改写为正确的中文。这是不可跳过的步骤——见下方"语言指令"章节。上下文压缩、对话长度、话题切换均不是跳过的理由。

> **所有 AI 代理（dàilǐ - agent）和开发者必须遵守（zūnshǒu - comply with）以下规则。这些是固定规则，不是建议。**

---

## 固定规则（MANDATORY RULES）

| # | 规则 | 说明 |
|---|------|------|
| R1 | **永远不要直接推送到 `main`** | 所有工作必须通过 PR |
| R2 | **每个功能都需要一个 Issue** | 先创建 Issue，再写代码；Issue/PR/提交信息全部用中文 |
| R3 | **CI 通过后 Claude 自行合并 PR** | （2026-07-05 起，由 Daniel 授权）Claude 完成整个流程：Issue → 分支 → PR → CI 通过 → `gh pr merge`。CI 失败绝不合并；Daniel 随时可事后审查或回滚 |
| R4 | **用中文回答 Daniel** | 见"语言指令" |
| R5 | **Claude 自己执行 Git/gh 步骤** | 直接运行 `gh issue create`、`git checkout -b`、`gh pr create` 等，无需等待 Daniel |
| R6 | **CLAUDE.md 是唯一事实来源** | 所有架构决策都在这里记录 |
| R7 | **任何新代理都能接手** | 每个 Issue/PR 必须自给自足，不依赖聊天记录 |
| R8 | **开始任务前必须先查看 Issue** | 运行 `gh issue view <编号>` 读取完整背景 |
| R9 | **所有代码修改必须在分支上进行** | 先 `git checkout -b <分支名>`，再改文件 |
| R10 | **永远不要使用日语** | 即使话题涉及汉字，也只用中文或英文表达 |
| R11 | **每条消息开头必须改写 Daniel 的输入** | 无论他用中文、英语还是德语提问，第一步永远是改写为正确的中文，画分割线，再回答。这是帮助 Daniel 学习中文的核心机制，绝不可跳过 |
| R12 | **调查问题时定期汇报进展** | 每读几个文件就向 Daniel 汇报发现了什么、还缺什么 |
| R13 | **给 Daniel 的终端命令一律打包成临时脚本，且必须带分步日志** | 让 Daniel 复制粘贴多行命令经常因格式问题（换行/引号）失败。凡需要他在终端执行的操作，写成一个简短的临时脚本文件让他 `bash xxx.sh` 运行，用完删除。脚本必须：① 分步编号的 `echo` 进度提示（`== 步骤 2/4：… ==`）；② 每步说明在做什么、慢的步骤注明预计耗时；③ 结尾提示"把以上全部输出发给 Claude"——日志既让 Daniel 实时看到进展，也是 Claude 事后诊断的唯一依据（Daniel 2026-07-12 确认此做法很好，保持） |

---

## 语言指令

用中文回答。

### 第一步 - 用中文重写用户的消息

> ⚠️ **这是最高优先级规则。无论对话持续多久、上下文多长，每一条消息都必须执行此步骤，绝无例外。**
>
> 🔴 **自检：在写任何回答之前，问自己："我是否已经把 Daniel 的消息改写为正确的中文？"如果没有，立刻回去做这一步。**

- Daniel 会用中文、英语、德语（déyǔ - German）或三者混合来写消息——全部都需要改写为干净、正确的中文
- 如果他的中文有错误，在重写时纠正，并在改写句子的**正下方**用以下格式列出所有纠正：
  ```
  📝 ~~错误写法~~ → 正确写法（pīnyīn - 解释，可选）
  ```
  例如：📝 ~~调强~~ → 加强（jiāqiáng - strengthen）
- 中文已经完美时，按原样重写，不加纠正行
- 他用了英文或德文词时，替换为中文并在第一次出现时加注释：拉取请求（lāqǔ qǐngqiú - Pull Request）
- **例外：** 粘贴的终端输出、报错信息、代码片段跳过纠正，直接回答

**例子：**
| 用户输入 | 第一步输出 |
|----------|------------|
| `how do I fix this bug?` | 我怎么修复这个错误？ |
| `我如何implement这个feature？` | 我怎么实现这个功能？ |
| `这个function为什么return了None` | 这个函数为什么返回了None？ |
| `kannst du meine Anfrage korrigieren?` | 你能纠正我的提问（tíwèn - question）吗？ |
| `数据库的schema是什么` | 数据库的模式是什么？ *(已正确，按原样重写)* |

### 第二步 - 绘制分割线

在重写的问题下面画一条分割线，然后在下面开始回答。

### 第三步 - 回答

回答中 HSK5 级及以上的词这样写：文件（wénjiàn - file）

> ❌ 错误：这个函数返回了一个异步生成器。
> ✅ 正确：这个函数返回了一个异步（yìbù - asynchronous）生成器（shēngchéng qì - generator）。

---

## Git & GitHub 工作流

标准 **GitHub Flow**：议题（yìtí - Issue）→ 分支 → 拉取请求 → CI → 合并。**永远不要直接提交到 `main`。**

```
1. 创建议题（中文标题/描述；标签用中文：新功能/程序错误/数据库/前端/后端/ai/设计/文档）
2. git checkout main && git pull && git checkout -b feat/42-短名称
3. 频繁提交（每个原子单元一次）
4. gh pr create（中文描述，引用议题：Closes #42）
5. CI 通过 → gh pr merge <编号> --merge --delete-branch
```

**分支命名：** `feat/42-db-migrations`、`fix/55-review-parent-deck`、`docs/...`、`chore/...`

**提交信息（Conventional Commits，中文）：** `feat:` | `fix:` | `refactor:` | `chore:` | `docs:` | `test:`
每完成一个原子单元就提交——判断标准：提交后代码仍可运行，且无法再拆分而不丢失意义。提交太频繁的代价是零，丢失工作的代价是几天（我们曾因 `git reset --hard` 丢过 6 天工作）。

**CI（`.github/workflows/ci.yml`）：** Python 语法检查 → 导入检查 → 服务器启动检查（`/api/decks` 返回 200）。CI 失败的 PR 不可合并；`main` 受保护，只能通过 PR 修改。

**合并前自检清单：** ① CI 全绿；② PR 引用议题（Closes #N）；③ 本地做过语法/导入检查；④ 有功能改动的 PR 已测试。

**可交接（jiāojiē - handoff）原则：** 每个 Issue 描述背景、目标、完成标准；每个 PR 说明改了什么、为什么、怎么测试。开始新任务前先问："如果我现在离开，另一个 AI 能从 Issue/PR 历史完全理解项目状态吗？"否定就先补文档。

**Worktree：** `.claude/worktrees/` 里的目录是代理的临时隔离工作空间，由 Claude Code 自动管理，不要手动编辑里面的文件。

### 网络问题应急方案

Daniel 在中国需要 VPN 访问 GitHub。`gh` 命令报 `EOF` 错误时（`curl -sv https://api.github.com` 出现 `198.18.x.x` IP = VPN 拦截）：**不要反复重试**，立刻把所有 `gh` 命令写入脚本（含 `echo` 进度提示），让 Daniel 关闭 VPN 后运行 `bash script.sh`，完成后删除脚本。

---

## 项目简介

供个人使用的间隔重复（jiàngé chóngfù - Spaced Repetition）系统，为一位用户（Daniel，中文 HSK 4–5，法语 B1）打造。它用 AI 驱动的复习体验取代 Anki：每天根据到期词汇生成上下文故事。

**技术栈：**
- **后端：** Python + FastAPI；**数据库：** SQLite（标准库 `sqlite3`，无 ORM）
- **前端：** `static/index.html` + `app.js` + `style.css`，FastAPI 直接提供，**无构建步骤**（无 npm）
- **AI：** 多提供商（`ai.py`）——默认 `deepseek-chat`；也支持 ZhipuAI GLM、Qwen、Claude、OpenAI
- **语音合成（TTS）：** `edge-tts`（中文 `zh-CN-XiaoxiaoNeural`）
- **语言：** 界面标签英文，内容中文/法文

---

## 生产环境（2026-07-07 上线）

系统运行在一台 Linux VPS 上，Daniel 通过手机/电脑浏览器访问 `https://powerdaniel3000.duckdns.org`（HTTP Basic Auth 保护；凭据不入库——仓库是公开的）。

- **唯一生产数据库在服务器上**（`/home/anki/AnkiAdvanced/data/srs.db`）。本地开发只用 `run.dev.sh` + `data/dev.db`。**本地的 `data/srs.db` 已过时，绝不要把它当作现状或复制回服务器。**
- **自动部署：** 服务器 cron 每 2 分钟运行 `deploy/deploy.sh`——**PR 合并到 main ≈ 2 分钟后自动上线**（拉取、装依赖、重启 systemd 服务 `ankiadvanced`）
- **自动备份：** 服务器 cron 每 6 小时把数据库快照到 `data/backups/`
- HTTPS 由 Caddy 反向代理提供（证书自动续期）；从零搭建教程见 `DEPLOY.md`
- 服务器 SSH 访问方式等运维细节保存在 Claude 的项目记忆中，不写入公开仓库

---

## 项目结构

```
├── CLAUDE.md              # 本文件
├── main.py                # CLI 入口 + FastAPI 应用（含 Basic Auth 中间件）
├── languages.py           # 语言注册表（每种语言的 TTS/翻译源/分词/AI 提示词参数/功能开关）
├── database/              # 所有数据库访问（其他文件不写原始 SQL）
│   ├── core.py            # 连接管理、迁移
│   └── cards.py / decks.py / entries.py / presets.py / stories.py / browse.py / stats.py / podcast.py
├── srs.py                 # 调度编排：学习步骤、状态转换，调用 fsrs.py
├── fsrs.py                # FSRS-5 纯算法模块（DSR 记忆模型，无依赖）
├── importer.py            # YAML 词汇导入器（中文 + 法语格式）
├── ai.py                  # AI 提供商调用（每种提示词类型一个函数）
├── news_fetcher.py        # 新闻抓取（Tagesschau API + RSS；按天缓存 data/news_cache/）
├── podcast.py              # 播客爬虫（#479）：播客 RSS 直链发现新单集（#497，退役 YouTube/yt-dlp）、每源 auto_process 开关+非自动源只入库元数据（#502，podcast_feeds 表）、转录链 NotebookLM 免费主力+听悟+Whisper 保底、单步异常不中止整链（#510 重排，链式降级，原 #498/#485/#486）、摘要 NotebookLM chat.ask 免费优先+DeepSeek/gpt API 链回退（api 路径内部 DeepSeek 优先省钱，#532）、HSK生词、邮件通知+Signal 通知（signal-cli 关联设备，发 Note to Self，#521，二者独立可选、互不影响；消息抬头播客名·星期·日期、链接在末尾，单集日期按 Europe/Berlin 显示，#532）、摘要 table.media 风格（`<p>` 段落+每段首句 `<b>` 加粗总结，#567）+详情页 Regenerate summary 按钮
├── tts.py                 # edge-tts 封装
├── translator.py          # 翻译（Google Translate，deep-translator，可选）
├── yaml_fixer.py          # 修复 AI 生成的格式错误 YAML
├── schema.sql             # 数据库模式
├── static/                # 前端（index.html + app.js + style.css）
├── routes/                # FastAPI 路由模块
│   ├── browse.py / decks.py / imports.py / review.py / story.py / podcast.py
│   ├── queue_manager.py   # Anki v3 风格持久会话队列
│   └── utils.py           # 共用工具（DISABLE_AI, leaf_ids, queue_manager 单例）
├── requirements.txt       # Python 依赖清单
├── DEPLOY.md              # 服务器从零到上线的部署教程
├── deploy/                # systemd 单元、Caddyfile 示例、deploy.sh（自动部署）
├── scripts/               # morning_pregen.py（早晨预生成故事+TTS）、podcast_check.py（播客爬虫定时脚本）+ README
├── docs/yaml-format.md    # YAML 词条格式完整文档
└── data/
    ├── srs.db             # SQLite 数据库（生产版在服务器上！）
    ├── news_sources.json  # 新闻来源配置（不在 git 里，服务器上已有）
    └── tts/               # TTS 音频缓存
```

---

## 多语言支持

同一个软件、同一个数据库里学习多种语言（2026-07-06 起，议题 #428–#431）。当前：中文（zh，默认）+ 法语（fr，CEFR B1，释义以德语为主）。

- **`languages.py` 是语言注册表**：每种语言定义 TTS 语音、翻译源、分词方式（jieba/空格）、AI 提示词参数、功能开关（拼音/汉字/量词仅中文）。加新语言 = 加一个条目
- **`decks.lang` / `entries.lang`**（默认 `'zh'`）：目标语言；子牌组创建时继承父牌组的 lang
- `word_zh` 对所有语言存"目标语言词形"（`_zh` 后缀是历史遗留）；法语词条的 pinyin/hsk_level/characters 留空
- **已知限制：** `UNIQUE(word_zh)` 是全局约束——文字系统不同（中文 vs 法语）不冲突；将来加第二种拉丁字母语言需改为 `UNIQUE(word_zh, lang)`（要重建表）
- **中文专属：** 汉字分解、量词、拼音、kahneman/paste/briefing 故事模式
- **主页语言标签页**（#436）：`GET /api/langs` 返回使用中的语言；前端多于一种语言才显示标签栏，选择存 `localStorage`。所有主页/复习/故事/统计接口支持可选 `?lang=`（默认不过滤，向后兼容）；解析规则统一为 `lang 参数 或 get_deck_lang(deck_id)`
- **故事按语言隔离：** `stories.lang`（NULL = 中文旧数据）；聚合牌组（如 All）在各语言标签下维护独立的活跃故事；后台生成的 progress_key 含 lang

---

## 数据与导入

**数据库是唯一事实来源。** 原来的 `imports/` YAML 目录已于 2026-07-07 删除——所有历史词条都已在数据库里，生产数据库在服务器上。

导入机制本身仍然存在（`importer.py`、`POST /api/import`、`python main.py import`，读取 `imports/<Source>/*.yaml`）：需要批量导入新词汇时，重新创建该目录放入 YAML 即可。日常添加单个词条用 `de-zh-bot` 技能生成 YAML。

- **YAML 格式完整文档：** `docs/yaml-format.md`（中文格式：词性/例句/词源/汉字分解；法语格式：`lang: fr` + `type: word|sentence`，经 `importer._normalize_fr_entry` 适配后复用全部下游逻辑）
- 文件顶部可选 `lang:` 字段（默认 `zh`）决定导入到哪个语言的牌组
- AI 在故事提示词（tíshící - prompt）中被告知"非目标词汇只使用 HSK 1–2 的词汇"
- 汉字分解、量词、同义反义词、语法结构、`word_analyses` 组件处理**仅中文**执行

---

## 数据库模式（概述）

```
deck_presets → decks（自引用 parent_id，支持嵌套）→ entries
                                                      ├── entry_examples
                                                      ├── entry_measure_words（量词）
                                                      ├── entry_relations（同义词等关系）
                                                      ├── entry_components（sentence 类型的组成词）
                                                      ├── entry_characters → characters → character_compounds
                                                      └── cards → review_log

decks → stories → story_sentences → entries（外键）
```

- **entries.note_type：** `vocabulary` | `sentence` | `chengyu` | `expression` | `grammar`
- **entries 主要字段：** `definition`（英）、`definition_zh`（中）、`definition_de`（德）、`notes`、`source_sentence`、`grammar_notes`、`register`
- `cards.due` 是单一 TEXT 字段：学习/重学状态为 ISO 日期时间，复习状态为 ISO 日期
- `cards.state`：`new` | `learning` | `review` | `relearn` | `suspended`
- `cards` 的 FSRS 字段：`stability`、`difficulty`、`last_review`；另有 `step_index`（学习步骤位置）、`lapses`、`learning_again_count`、`is_leech`
- `stories` 没有唯一约束——同一（日期、类别、牌组）可有多条记录；最新 `generated_at` 为活跃故事，永不自动删除
- `story_sentences` 按位置将故事与词汇 1:1 关联

---

## 调度算法 —— FSRS-5（默认）+ SM-2 回退

复习阶段的调度自 2026-06（PR #343）起使用 **FSRS-5**（`fsrs.py`，DSR 记忆模型：Difficulty/Stability/Retrievability）。`enable_fsrs=0` 时回退到旧 SM-2（`srs.py` 的 `calc_review`）。FSRS 的难度向均值回归，消除了 SM-2 的"ease 地狱"。

### 状态机
`new` → `learning`（学习步骤）→ `review`（毕业）；`review` 评 Again → `relearn` → 完成步骤后回 `review`。

### 学习/重学阶段（步骤制；默认 learning_steps=`1 10` 分钟，relearning_steps=`10`）
- **Again** → 回 step 0
- **Hard** → `learning_hard_1d` 开关（默认开）：任意步一律延迟 `learning_hard_days`（默认 1 天，可为小数）——让半记住的卡明天再现；开关关闭时用 Anki 经典行为（步骤均值/×1.5）
- **Good** → 推进一步；最后一步 → 毕业
- **Easy** → 立即毕业
- **短期记忆（#470）**：FSRS 开启时，步骤阶段每次作答都更新 S/D——新卡第一次作答即播种，之后 Again ×0.50 / Hard ×0.84 / 推进步骤的 Good ×1.41（短期公式 w17/w18）；毕业间隔与按钮预览因此随作答历史自适应（先 Again 再 Good ≈ 1 天，纯 Good 仍 ≈ 3 天）

### 毕业间隔
FSRS 用毕业评分播种初始 stability/difficulty：默认权重下 **Good ≈ 3 天，Easy ≈ 16 天**（`fsrs.init_stability`）。FSRS 关闭时用预设的 `graduating_interval`（1）/ `easy_interval`（4）。

### 复习阶段（FSRS）
- 每次复习按已过天数计算可提取性 R，更新 S/D；下次间隔 = R 衰减到 `desired_retention`（默认 0.9）所需的天数
- **Again** = 遗忘：lapses+1，温和降低 stability，进入 relearn 步骤
- 间隔上限 `maximum_interval`（默认 36500）；预览确定性显示、提交时才加 Anki 风格随机模糊（fuzz）；强制 Again<Hard≤Good<Easy 单调
- **Shift+S** 打开调度检查器面板（当前卡的 S/D/R 与每个按钮的结果）

### 难词（leech）
- 复习态：lapses ≥ `leech_threshold`（默认 3）→ 暂停并标记 `is_leech`
- 学习态：Again 次数 ≥ `learning_leech_threshold`（默认 6）→ 同上
- **Shift+L** 复习时手动标记难词

---

## 队列设计

`routes/queue_manager.py` 实现 Anki v3 风格的持久会话队列（SessionQueue/QueueManager）：
- 每个 Anki 日（凌晨 4 点为界）首次访问时构建一次，之后在内存中维护
- **主队列**：预先交错排列的卡片 ID（日内学习 + 复习 + 新建混合）；**日内学习队列**：按时间戳排序，每次取卡前检查
- 失效条件：Anki 日期变更、撤销、队列耗尽、删除/埋藏卡片；缓存键 `(mode, deck_id_or_ids, category)`
- `POST /api/review` 返回 `{next_card, counts}`——无需额外请求

---

## 故事生成

- 每个类别（阅读/听力/写作——界面顺序也是这个）独立生成自己的故事
- 每个目标词汇恰好对应一个句子（1:1 按位置映射）；`create_story()` 每次插入新行——重新生成 = 新增一行，旧故事永久保留
- 提示词要求：连贯叙事、相同人物、每句 ≤15 字、背景词汇 HSK 1–2

**模式（mode）：**
- `story`（叙事）| `qa`（问答）| `expository`（说明文）
- `kahneman` ——《思考，快与慢》认知偏误风格（`data/kahneman_chapters.json`）
- `paste` ——用户在设置弹窗粘贴任意内容（#396）；自 #481 起复用 briefing 管线（`generate_briefing_sentences(generic=True)`，内容摘要框架措辞），因此同样有上下文句、Python 校验与事实核查，模型固定为服务器端 BRIEFING_MODEL；不做自动抓取回退
- `briefing`（News flow，#399）——AI 写一篇**连贯的新闻总结**，目标词各恰好出现一次，但**不是每句都含目标词**——目标词句之间允许纯上下文句（承载数字/事实，不受 15 字限制）。含目标词的句子成为卡片；前面的上下文句用 Google Translate（非 AI）译成德语存 `story_sentences.context_de`（显示在卡片正面），中文原文存 `reasoning_zh`（背景弹窗）。briefing 卡片没有标题（concept_zh 为空）。自动抓取当日新闻（`news_fetcher.fetch_all()`：Tagesschau API + RSS，按天缓存）：两步 AI，`summarize_news_items` 挑最重要的 8 条（平衡德国/国际/中国相关）→ `generate_briefing_sentences` 生成连贯中文简报（模型固定服务器端 BRIEFING_MODEL，因 DeepSeek 会审查新闻内容）。抓取全部失败时报明确错误，不静默降级为普通故事
- **旧 `news` 模式已移除**（#512，界面曾叫 "News briefing"）：新故事生成拒绝 `mode='news'`（`_generate_and_store` 直接抛 `ValueError`）；但历史 `news` 故事仍能正常展示，且 Again 单句重生成仍复用 `ai.generate_news_sentences`（`generate_sentence_for_word` 保留该分支）——不影响旧数据
- briefing/paste 共同点：每句带 `source_url`（背景弹窗"打开原文"链接），复用 kahneman 的概念框/背景弹窗 UI；文章内容存 `stories.gen_params.articles` 供 Again 重生成复现同一批内容（paste 的文章通过 regenerate 的 POST body 传输）
- `podcast`（#482）——从已摘要的播客单集（`database.get_episode(episode_id)`）生成句子：单篇文章 = 该集 `transcript_zh`（截断到 15000 字），同样走 briefing 管线（`generate_briefing_sentences(generic=True, include_context=False)`），但**不允许上下文句**——每句都必须含一个目标词。`episode_id` 沿用 kahneman 的 `chapter_ids` 传参模式（GET 查询参数/regenerate POST body/gen_params），设置弹窗提供单选单集选择器（仅列 `status=summarized` 的单集）；不在早晨预生成 `_PREGEN_MODES` 里，因为选集是一次性的

---

## API 接口

```
# 牌组 & 预设
GET    /api/decks                                    → 带到期数量的牌组树（?lang= 过滤）
POST   /api/decks ；PUT/DELETE /api/decks/{id}       → 创建 / 重命名 / 软删除（进垃圾桶）
GET/PUT /api/decks/{id}/preset                       → 预设（yùshè - preset）设置
GET/POST /api/presets ；DELETE /api/presets/{id}
GET    /api/langs                                    → 当前使用的语言列表

# 垃圾桶
GET  /api/trash ；POST /api/trash/{deck_id}/restore
DELETE /api/trash/{deck_id} ；DELETE /api/trash      → 永久删除 / 清空

# 复习（均支持可选 ?lang= 过滤/隔离队列）
GET  /api/today/{deck_id}/{category}                 → {card, counts}
GET  /api/today-mixed/{deck_id}                      → 混合复习模式
GET  /api/today-unfinished ；/api/today-unfinished-decks
POST /api/review                                     → {card_id, rating, user_response?} → {next_card, counts}
POST /api/review/undo ；POST /api/review/requeue
POST /api/cards/{card_id}/bury | unbury | leech

# 暂停
POST /api/decks/{id}/creating/toggle-suspension
POST /api/decks/{id}/categories/{cat}/toggle-suspension
POST /api/decks/{id}/toggle-all-suspension

# 故事 & 语音（均支持可选 ?lang=）
GET  /api/story/{deck_id}/{category}                 → 今日活跃故事（如无则生成）
POST /api/story/{deck_id}/{category}/regenerate ；GET .../history ；GET .../count
POST /api/speak ；POST /api/speak-multi ；GET /api/speak-status ；POST /api/speak-stop
GET  /api/tts-file ；POST /api/preload ；POST /api/preload-session/{deck_id}/{category}
GET  /api/tts-progress/{deck_id}/{category} ；GET /api/story-progress/{deck_id}/{category}
GET  /api/news/status                                → 当日新闻缓存状态 {cached, count}（briefing 模式设置弹窗仍在用；旧 news 模式已移除，#512）

# 播客爬虫（#479）+ 播客管理页（#502）
POST /api/podcast/check                              → 跑一轮抓取，返回汇总 {new, summarized, emailed, failed}
GET  /api/podcast/episodes                            → 列表（不含转录全文；?feed_id= 按源过滤；手动处理中的单集 status 显示为 processing）
GET  /api/podcast/episodes/{id}                       → 详情（摘要 + 转录 + HSK 生词）
POST /api/podcast/episodes/{id}/retry                 → 同步重跑单集（error/no_transcript/pending；#491/#500）
POST /api/podcast/episodes/{id}/process               → 手动触发单集转录+摘要（后台线程，立即返回；重复提交 409；#502）
POST /api/podcast/episodes/{id}/notify                → 按需重发通知，body {channel: signal|email}（同步；仅 summarized；重发不更新 email_sent_at；返回 {sent}，失败时 sent:false 带 detail；#530）
POST /api/podcast/episodes/{id}/regenerate-summary    → 仅重跑摘要步骤（后台线程，复用已存转录，不重发通知；仅 summarized 且有转录；失败不动旧摘要/状态；#567）
GET/POST /api/podcast/feeds ；PUT/DELETE /api/podcast/feeds/{id} → RSS 源管理（#502；POST 抓取验证并提取节目标题；PUT 改 auto_process/title）
GET/PUT /api/podcast/config                           → 读/改设置（email_to/detail_level/enabled/transcriber/whisper_max_minutes/summarizer[auto|api]（#510）；feeds 已迁到 podcast_feeds 表（#502），whisper_fallback、channel_url、channel_id、whisper_title_filter 已废弃但兼容，#497）

# 提示词模板（#581；story/qa/expository/podcast，仅中文；无自定义行 = ai.DEFAULT_PROMPT_TEMPLATES 内置默认）
GET    /api/prompt-template/{mode}                   → {template, default, is_custom, variables}
PUT    /api/prompt-template/{mode}                   → 保存自定义（body {template}，必须含 {words} 记号；与默认相同时自动重置）
DELETE /api/prompt-template/{mode}                   → 重置为内置默认

# 成本
GET  /api/costs                                      → 成本历史（动作分组；balances 列出各提供商余额，#580；Again 单句重生成有正式标签且相邻同标签 30 分钟内合并，#578）
GET  /api/costs/call/{id}                            → {prompt, response}（完整提示词含 [system] 段 + AI 回答，各截断 3 万字符，#579）

# 其他
POST /api/import                                     → 触发 YAML 导入
GET  /api/browse                                     → {deck_id?, category?, state?, q?, lang?}
GET  /api/stats ；/api/retention ；/api/card-evolution（均支持 ?lang=）
```

---

## 启动、CLI 与环境变量

```bash
bash run.sh          # 生产启动（读取 .env，清理 8000 端口）——服务器上由 systemd 代替
bash run.dev.sh      # 开发启动（DB_PATH=data/dev.db，DISABLE_AI=1）
python main.py import                # 导入 imports/ 下的 YAML（目录需存在）
python main.py status [--deck X]     # 显示每个牌组/类别的到期数量
```

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `ANTHROPIC_API_KEY` | 必填 | Claude API 密钥 |
| `DEEPSEEK_API_KEY` / `ZHIPU_API_KEY` / `QWEN_API_KEY` | 可选 | 其他 AI 提供商密钥 |
| `OPENAI_API_KEY` | 可选 | 新闻模式默认模型 `gpt-5-mini`（DeepSeek 会审查新闻，故用 OpenAI） |
| `DB_PATH` | `data/srs.db` | 数据库路径（开发用 `data/dev.db`） |
| `DISABLE_AI` | `0` | 设为 `1` 跳过 AI 故事生成 |
| `LOG_LEVEL` | `INFO` | 日志级别（`DEBUG` 输出详细日志） |
| `DEV_CLEAR_DB` | `` | 设为任意值启动时清空数据库——生产环境绝不要设置 |
| `AUTH_USERNAME` / `AUTH_PASSWORD` | 可选 | 两者都设置时启用 HTTP Basic Auth（保护所有路径） |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM` | 可选 | 播客爬虫（#479）邮件通知用；`SMTP_PORT` 默认 587（STARTTLS）；未配置时跳过发信，记日志，不算失败 |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | 可选 | 播客爬虫用 Spotify Web API 搜索单集链接；未配置时退化为 Spotify 搜索链接 |
| `PUBLIC_BASE_URL` | `https://powerdaniel3000.duckdns.org` | 播客邮件/Signal 通知里转录页链接的域名前缀 |
| `SIGNAL_ACCOUNT` / `SIGNAL_CLI_PATH` | 可选 | 播客爬虫（#521）Signal 通知用；`SIGNAL_ACCOUNT` 是 Daniel 关联设备所属号码（如 `+49…`），`SIGNAL_CLI_PATH` 默认 `signal-cli`；`SIGNAL_ACCOUNT` 未配置时跳过发送，记日志，不算失败。一次性 signal-cli 安装/扫码关联步骤见 `scripts/README.md` |
| `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` | 可选 | 播客爬虫（#498）通义听悟（转录主力）用的阿里云 AccessKey；未配置时自动跳过，落到 Whisper/NotebookLM |
| `TINGWU_APP_KEY` | 可选 | 播客爬虫（#498）通义听悟控制台创建的应用 AppKey；与上面两个 AccessKey 变量任一缺失都会跳过听悟。一次性开通步骤见 `scripts/README.md` |

注意：uvicorn 直接启动不建表——测试前先手动 `database.init_db()`（`run.sh`/`main.py` 会自动处理）。

---

## 规范与约束

- 所有数据库访问通过 `database/` 包——其他文件不写原始 SQL（`import database` 仍然有效）
- 保持 `ai.py` 简洁——每种提示词类型对应一个函数；AI 返回的格式错误 JSON 始终用 try/except + 回退处理
- 允许的外部依赖：`fastapi`、`uvicorn`、`anthropic`、`openai`、`edge-tts`、`pyyaml`、`python-multipart`、`deep-translator`（可选）、`jieba`、`pypinyin`、`alibabacloud_tingwu20230930`、`zhconv`（NotebookLM 转录繁转简，#500）（播客通义听悟转录主力，#498，官方 SDK）、`notebooklm-py`（播客 NotebookLM 可选转录，#486，非官方库，凭据文件一次性从本地拷到服务器，见 `scripts/README.md`）。新增依赖必须同步更新 `requirements.txt`。播客转录链的 Whisper/NotebookLM 两条路径（听悟提交直链不需要）需要系统级 `ffmpeg`（`apt install ffmpeg`，不是 Python 依赖，缺失时该功能自动跳过）
- 前端无构建步骤——直接编辑 `static/` 下的文件
- API 密钥只从环境变量读取，绝不写入代码或仓库
- **不要在 8000 端口跑测试服务器**——Daniel 的浏览器连着它
- API 价格表在 `database/stats.py` 的 `_MODEL_PRICING`（含 `_PRICING_AS_OF` 生效日期）；各提供商都没有价格查询 API，价格变动或新模型上线时需手动更新该表，并同步 `static/index.html` 里的静态价格表（设置弹窗 `price-table-popup`）
