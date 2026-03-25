# 中文间隔重复系统 — 项目说明

> **所有 AI 代理（dàilǐ - agent）和开发者必须遵守（zūnshǒu - comply with）以下规则。这些是固定规则（gùdìng guīzé - fixed rules），不是建议。**

---

## 目录（mùlù - Table of Contents）

1. [代理必须遵守的固定规则](#代理必须遵守的固定规则mandatory-agent-rules)
2. [Git & GitHub 工作流](#git--github-工作流mandatory)
3. [Claude 对 Git 工作流的态度](#claude-对-git-工作流的态度)
4. [可交接原则](#可交接jiāojiē---handoff原则)
5. [Worktree 使用说明](#worktree工作树使用说明)
6. [网络问题应急方案](#网络问题应急方案)
7. [语言指令](#语言指令)
8. [项目简介 & 技术细节](#项目简介)

---

## 代理必须遵守的固定规则（MANDATORY AGENT RULES）

每一个接手（jiēshǒu - take over）这个项目的 AI 代理都必须遵守以下规则，不得例外（lìwài - exception）：

| # | 规则 | 说明 |
|---|------|------|
| R1 | **永远不要直接推送到 `main`** | 所有工作必须通过 PR |
| R2 | **每个功能都需要一个 Issue** | 先创建 Issue，再开始写代码；Issue/PR/提交信息全部用中文 |
| R3 | **永远不要创建或合并 PR** | Daniel 亲自创建 PR、审核并合并 —— Claude 只提供命令供 Daniel 运行 |
| R4 | **用中文回答 Daniel** | 见"语言指令"部分 |
| R5 | **引导 Daniel 执行 Git 步骤，不要替代他** | 给命令，让他自己运行 |
| R6 | **CLAUDE.md 是唯一事实来源** | 所有架构决策都在这里记录 |
| R7 | **任何新代理都能接手** | 每个 Issue/PR 必须自给自足，不依赖聊天记录 |
| R8 | **开始任务前必须先查看 Issue** | 运行 `gh issue view <编号>` 读取完整背景，不得依赖用户口头描述 |
| R9 | **所有代码修改必须在分支上进行** | 永远不要在 `main` 分支上直接修改文件；先用 `git checkout -b <分支名>` 创建分支 |

---

## Git & GitHub 工作流（MANDATORY）

我们遵循（zūnxún - follow）**GitHub Flow** —— 大多数专业（zhuānyè - professional）软件团队使用的标准工作流。
所有工作都通过：议题（yìtí - Issue）→ 分支（fēnzhī - Branch）→ 拉取请求（lāqǔ qǐngqiú - Pull Request）→ 审核（shěnhé - Review）→ 合并（hébìng - Merge）。
**永远不要直接提交（tíjiāo - commit）到 `main`。**

### 每项工作的完整流程（liúchéng - flow）

```
1. 议题存在（或创建一个）   →   追踪"做什么"和"为什么"
2. 从 main 创建分支        →   隔离（gélí - isolated）的工作空间
3. 频繁提交               →   小的、安全的检查点
4. 开一个 PR（引用议题）   →   "这是我做的"
5. CI 自动运行            →   发现明显的问题
6. Daniel 审核并批准      →   人工质量关卡
7. 合并到 main            →   完成，议题自动关闭
```

### 议题（Issues）

每项任务都从一个 GitHub Issue 开始。议题追踪**做什么**和**为什么**——它们是事实来源（láiyuán - source of truth），不是聊天消息。

- 开始任何重要工作之前先创建一个议题
- **议题标题和描述用中文写**
- 议题有**标签（biāoqiān - labels）**：`feature`、`bug`、`database`、`frontend`、`backend`
- 议题按**里程碑（lǐchéngbēi - milestone）**分组（如"Recovery Sprint"）
- 开 PR 时，始终引用（yǐnyòng - reference）议题：`Closes #42`
  这会在 PR 合并时自动关闭议题。

**议题标题示例：**
```
feat: 给牌组列表添加垃圾桶功能
fix: 修复父级牌组复习时卡片重复的问题
chore: 升级 edge-tts 到最新版本
```

### 分支（Branches）

分支名称包含议题编号，便于追踪：

```
feat/42-db-migrations
feat/43-ai-provider
fix/55-review-parent-deck
```

命令（mìnglìng - commands）：
```bash
git checkout main && git pull        # 始终从最新的 main 开始
git checkout -b feat/42-db-migrations
```

### 提交（Commits）—— Conventional Commits 格式

**提交信息用中文写。** 格式：`<类型>: <简短的祈使句描述>`

```
feat: 给卡片和牌组添加软删除字段
fix: 从复习队列中排除已软删除的卡片
refactor: 将 leaf_ids 提取为公共工具函数
chore: 添加 openai 依赖以支持多模型
docs: 更新 CLAUDE.md 的工作流说明
test: 为复习接口添加集成测试
```

类型（lèixíng - types）：`feat` | `fix` | `refactor` | `chore` | `docs` | `test`

**每完成一个逻辑单元就提交** —— 一个函数、一个文件、一次数据库迁移（qiānyí - migration）。
提交太频繁的代价是零。丢失工作的代价是几天时间。

### 拉取请求（Pull Requests）

- 使用 `gh pr create` 创建 PR
- **PR 标题和描述用中文写**
- PR 模板（`.github/pull_request_template.md`）指导描述内容
- 每个 PR 必须：引用议题、描述变更（biàngēng - changes）、列出如何测试
- PR 只有在 Daniel 批准后才能合并 —— Claude 永远不合并

**PR 描述示例：**
```
## 变更内容
- 添加了垃圾桶 API（软删除 + 恢复 + 永久删除）
- 前端添加垃圾桶弹窗

## 测试方法
- 删除一个牌组，确认它出现在垃圾桶里
- 恢复该牌组，确认卡片数据完整

Closes #43
```

### CI（GitHub Actions）

每个 PR 自动触发（chùfā - trigger）`.github/workflows/ci.yml`：
1. 对所有 `.py` 文件进行 Python 语法检查
2. 导入检查（模块导入不崩溃）
3. 服务器启动检查（访问 `/api/decks`，必须返回 200）

如果 CI 失败（shībài - fail），PR 不可合并。修复问题，再次推送（tuīsòng - push），CI 重新运行。

### 受保护（shòu bǎohù - protected）的 `main` 分支

`main` 始终可以运行。它在 GitHub 仓库（cāngkù - repository）设置中配置为：
- 必须通过 PR（不能直接推送）
- 必须通过 CI 才能合并

### 分支命名（mìngmíng - naming）
- 功能：`feat/42-db-migrations`、`feat/43-ai-provider`
- 修复（xiūfù - bug fix）：`fix/55-review-parent-deck`
- 前端：`feat/60-frontend-trash`

### 为什么这很重要
我们曾因为对未提交的更改执行 `git reset --hard` 而丢失了 6 天的工作。
这个工作流让这种情况不可能发生：每次提交都安全地保存在远程（yuǎnchéng - remote），
每个功能都可以审核，`main` 始终可以部署（bùshǔ - deploy）。

---

## Claude 对 Git 工作流的态度（tàidù - attitude）

**Daniel 应该亲自（qīnzì - himself）操作 Git 工作流的每一步。**

Claude 的角色是：
- **引导（yǐndǎo - guide）**，而不是替代（tìdài - replace）
- 提醒 Daniel 下一步该做什么，但由 Daniel 自己执行（zhíxíng - execute）
- 如果 Daniel 跳过步骤（比如没有创建议题就开始写代码），Claude 应该温和地（wēnhé de - gently）指出来

具体来说（jùtǐ lái shuō - specifically）：

| 步骤 | Claude 的做法 |
|------|--------------|
| 创建议题 | 提醒 Daniel 运行 `gh issue create ...`，给出命令但不自动执行 |
| 创建分支 | 提示正确的分支名格式，但让 Daniel 运行 `git checkout -b ...` |
| 提交 | 在合适的时机提醒 Daniel 提交，建议提交信息 |
| 开 PR | 提供 `gh pr create` 命令，让 Daniel 执行 —— Claude 永远不创建 PR |
| 合并 | 永远不要自动合并 —— 始终等待 Daniel 在 GitHub 上批准并合并 |

> **关键原则（yuánzé - principle）：PR 的创建和合并完全由 Daniel 本人（běnrén - himself）负责。Claude 只给命令，Daniel 自己执行。**

**当 Daniel 问"下一步做什么？"时，Claude 应该用工作流的语言回答：**
> "下一步：为这个功能创建一个 Issue，然后从 main 创建分支。你想用什么标题？"

不要直接说"我来帮你创建分支"——而是说"你可以运行：`git checkout main && git pull && git checkout -b feat/XX-name`"。

---

## 可交接（jiāojiē - handoff）原则

**这个项目的所有工作都应该写得让任何人或任何 AI 代理（dàilǐ - agent）可以接手并继续。**

这意味着：

### 每个 Issue 必须自给自足（zì jǐ zú - self-contained）
- 清晰描述背景（bèijǐng - context）、目标、完成标准（biāozhǔn - criteria）
- 不依赖口头约定或聊天记录
- 任何人看到 Issue 就知道该做什么

### 每个 PR 必须可独立（dúlì - independent）理解
- PR 描述说明：改了什么、为什么改、怎么测试
- 不需要问作者就能理解变更意图（yìtú - intent）

### CLAUDE.md 是唯一的真相来源
- 架构决策（juécè - decisions）、约定、约束都在这里
- 新来的 Claude 或开发者读完 CLAUDE.md 就能上手
- 不要把重要信息只放在聊天记录里

### Claude 的行为标准（xíngwéi biāozhǔn - behavioral standard）
当开始一项新任务时，Claude 应该先问：
> "如果我现在离开，另一个 AI 能从 GitHub 的 Issue 和 PR 历史中完全理解这个项目的状态吗？"

如果答案是否定的，先补全文档（wéndàng - documentation），再写代码。

---

## Worktree（工作树）使用说明

Worktree 允许（yǔnxǔ - allow）git 同时在多个目录检出（jiǎnchū - check out）不同分支，每个目录完全独立（dúlì - independent）。

### Claude Code 何时自动使用 Worktree

- 当一个代理需要修改文件，但不能影响你的主工作目录时
- 当多个代理并行（bìngxíng - parallel）工作时，每个代理有自己的隔离（gélí - isolated）副本
- 当 Claude Code 的 Agent 工具使用 `isolation: "worktree"` 参数启动时

### 你会看到什么

```
项目根目录/
└── .claude/worktrees/
    ├── sleepy-elion/    ← 某个代理正在这里工作
    └── vibrant-benz/    ← 另一个代理的隔离空间
```

工作完成后：
- **如果有变更** → 代理将结果整合（zhěnghé - integrate）成一个新分支和 PR
- **如果没有变更** → Worktree 自动清理，目录消失

### 你不需要手动操作 Worktree

这完全由 Claude Code 管理。你唯一需要知道的是：

> `.claude/worktrees/` 里的目录是代理的临时（línshí - temporary）工作空间，不要手动编辑里面的文件。

### 分支命名规则

Worktree 里的分支和普通（pǔtōng - regular）分支遵循同样的规范（guīfàn - convention）：
```
feat/42-feature-name
fix/55-bug-name
```

---

## 网络问题应急方案

Daniel 在中国需要使用 VPN 才能访问 GitHub。VPN 有时不稳定，导致 `gh` 命令报 `EOF` 错误。

### 诊断（zhěnduàn - diagnose）步骤

```bash
# 1. 确认 gh 认证状态
gh auth status

# 2. 测试 API 连通性
gh api rate_limit

# 3. 测试 TLS 连接（如果 gh 失败）
curl -sv https://api.github.com 2>&1 | tail -5
```

**`198.18.x.x` IP 出现** = VPN 正在拦截请求，TLS 握手失败。

### 应急流程（liúchéng - flow）

当 Claude 无法直接运行 `gh` 命令时：

1. **Claude** 将所有命令写入 `create_issues.sh`（或类似脚本文件）
2. **Daniel** 暂时关闭 VPN
3. **Daniel** 运行脚本：`bash create_issues.sh`
4. **Daniel** 重新开启 VPN

### 脚本模板

```bash
#!/bin/bash
set -e
echo "===== 创建标签 ====="
gh label create "标签名" --color "颜色" --description "描述" --force
echo "✓ 标签创建完成"

echo "===== 创建议题 ====="
gh issue create \
  --title "标题" \
  --body "内容" \
  --label "标签"
echo "✓ 议题创建完成"
```

### Claude 的责任

- 遇到 EOF 错误时，**不要反复重试**，立刻生成脚本文件
- 脚本要包含 `echo` 进度提示，方便 Daniel 知道执行到哪一步
- 脚本执行完成后，Claude 继续后续工作

---

## 语言指令

用中文回答。

### 第一步 - 用中文重写用户的消息

> ⚠️ **这是最高优先级规则。无论对话持续多久、上下文多长，每一条消息都必须执行此步骤，绝无例外。上下文压缩不是跳过此步骤的理由。**

- **这一步是强制的（qiángzhì de - mandatory），无论用户用什么语言提问，都必须先执行**
- Daniel 会用中文、英语、德语（déyǔ - German）或三者混合来写消息——全部都需要纠正
- 总是从将用户的输入改写为一个干净、正确的中文句子开始
- 如果用户使用英语、德语或混合语言（例如："我如何implement这个feature？"），将其翻译/改写为中文
- 如果用户用中文写了错误，在重写时默默地纠正它们，不要单独解释错误
- 如果用户的中文已经很完美了，就按原样重写
- **如果用户在消息中用了英文或德文词，在重写时把它们替换为中文，并在第一次出现时加上注释格式：** 拉取请求（lāqǔ qǐngqiú - Pull Request）
- **例外（lìwài - exception）：** 如果用户粘贴的是终端输出、报错信息、代码片段，则跳过纠正，直接回答

**例子：**
| 用户输入 | 第一步输出 |
|----------|------------|
| `how do I fix this bug?` | 我怎么修复这个错误？ |
| `我如何implement这个feature？` | 我怎么实现这个功能？ |
| `这个function为什么return了None` | 这个函数为什么返回了None？ |
| `我想add一个新的endpoint` | 我想添加一个新的接口。 |
| `数据库的schema是什么` | 数据库的模式是什么？ *(中文已正确，按原样重写)* |
| `perfect. another question: why is X not working?` | 完美。另一个问题：为什么 X 不起作用？ |
| `kannst du meine Anfrage korrigieren?` | 你能纠正我的提问（tíwèn - prompt/question）吗？ |
| `请处理这个Issue, mach das bitte` | 请处理这个议题（yìtí - issue），谢谢。 |

### 第二步 - 绘制分割线
在重写的问题下面，画一条分割线，格式如下：

**纠正的问题**

--------------------------------------------

*(然后在分割线下面开始回答)*

### 第三步 - 回答
在回答中，HSK5级及以上的词请这样写：文件（wénjiàn - file）

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