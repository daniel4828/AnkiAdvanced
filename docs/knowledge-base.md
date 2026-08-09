# 知识库（Knowledge Base）总体设计

> 把现有的「播客」功能泛化成一个统一的知识库：播客单集、YouTube 视频、报刊文章
> 三类素材走**同一条流水线**（获取 → 转录/正文 → 中文+德语摘要 → 生词标注 →
> 通知 → 造卡）。本文件是这个功能的唯一设计说明，各阶段 Issue 都引用它。

---

## 核心判断：不新建表，泛化现有表

`podcast_episodes` 已经承载了整条链路所需的全部列（`transcript_zh`、
`summary_de`、`summary_zh`、`hsk_words`、`status`、`transcript_source`…）。
YouTube 视频和文章的**唯一区别是「怎么拿到中文正文」**，下游 100% 相同。

因此：

- **表名保持 `podcast_episodes`**，加一列 `kind`（`podcast` | `video` | `article`）。
  改表名要重建表 + 迁移生产库，风险远大于收益。本仓库已有多处同类先例
  （`youtube_url` 列现在存播客网页链接、`word_zh` 对法语存法语词形）。
- **`database.get_episode(id)` 保持不变** → 造卡侧（`routes/story.py` 的
  podcast 模式）几乎零改动就能吃到视频和文章。
- 列名的历史含义在 `schema.sql` 注释里写清楚，不要重命名。

### 列的新含义

| 列 | podcast | video | article |
|---|---|---|---|
| `kind` | `podcast` | `video` | `article` |
| `video_id` | RSS guid | YouTube video id | 规范化后的 URL（去 utm 参数）|
| `channel_id` | RSS feed URL | YouTube 频道 id（拿得到就存）| 站点域名 |
| `youtube_url` | 单集网页 | 视频链接 | 文章链接 |
| `audio_url` | mp3 直链 | NULL | NULL |
| `transcript_zh` | 转录 | 字幕全文 | 正文全文 |
| `transcript_source` | tingwu/whisper/notebooklm | `captions` | `article` |
| `title_en`（新列）| AI 译的英文标题 | 同左 | 同左 |

## 素材来源与投递渠道

三个渠道，**分阶段做，不要一次全上**：

1. **界面输入框**（阶段 B 一起做）：知识页顶部粘贴 URL → 「添加」。零新依赖、
   零故障点，手机浏览器也能用（已有 HTTPS + Basic Auth）。
2. **邮件收件**（阶段 F，最后做）：手机「分享 → 邮件」发到专用邮箱，服务器
   cron 用 `imaplib` 轮询取 URL 入库。
3. **Signal 接收**：**不做**。`signal-cli` 需要常驻 `receive` 轮询，关联设备掉线
   会静默停摆，比发送脆弱得多。

## 生词：只信 `zh_annotate`，不信 AI（修 Daniel 报的「不准」）

现状有**两套并存**的生词逻辑，这就是不准的根因：

- 正文标注 → `zh_annotate.py`（#638，确定性代码，已查 `entries.word_zh`）✅
- 底部表格 `hsk_words` → **AI 在摘要提示词里自己挑的**，漏词、挑已学过的词 ❌

阶段 A 统一到 `zh_annotate`：表格由代码从 `summary_zh` + `summary_de` 里的中文
扫描生成，与正文标注**同源同规则**，看到的括号注释和表格里的行一一对应。

**生词判定规则**（维持 #638 现状，Daniel 2026-08-09 确认）：
不在 `entries.word_zh` **且**（HSK ≥ 5 **或** 不在 4991 词的 HSK 表里）；
表外词再过一道「每个字都是 HSK≤4 就跳过」的透明组合过滤，
挡掉 `十年`/`巨大变化` 这类刷屏噪音。

## 成本（Daniel 问的）：不用担心

一小时素材 ≈ 1.5 万字 ≈ 1.1 万 token。DeepSeek 输入 $0.27/M：

- 摘要一次 ≈ **$0.003**
- 造卡再发一次全文 ≈ **$0.003**

整篇转录直接喂给模型完全不心疼，**不需要为省钱做截断优化**。现有的 15000 字
截断保留即可（那是为了上下文窗口，不是为了钱）。

---

## 阶段划分

| 阶段 | Issue | 内容 | 依赖 |
|---|---|---|---|
| A | #650 | 数据模型泛化（`kind`/`title_en` 列）+ 生词统一到 `zh_annotate` | — |
| B | #651 | YouTube 摄取（字幕 API + oEmbed 标题）+ `POST /api/knowledge/add` | A |
| C | #652 | 文章摄取（正文抽取） | B |
| D | #653 | 前端：播客页 → 知识页（播客/视频/文章三个子标签） | A |
| E | #654 | 造卡：故事的 podcast 模式 → 知识库模式（按类型筛选选素材） | A、D |
| F | #655 | 邮件收件（IMAP 轮询） | B、C |

每阶段一个分支、一个 PR、CI 绿了才合并。
