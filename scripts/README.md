# scripts/

## morning_pregen.py（issue #420，issue #458 重构）

新闻 / 简报模式的故事生成很慢（多次串行 AI 调用），Daniel 早上打开页面时
不想等。这个脚本对**运行中的服务器**发一次 `POST /api/pregen-today` 请求，
由服务器端"重复最近一天真正用过的故事键"：

- 服务器找到最近一天（今天除外，最多回看 14 天）所有真正生成过的故事键
  `(deck_id, category, lang)`——即 Daniel 昨天实际复习用到的牌组/类别/模式
  组合（包括 briefing/news 等聚合牌组模式），各键沿用上次的生成参数
  （mode/topic/grammar 等；news/briefing 的 articles 被丢弃，重新抓当天新闻）
- 每个键：今天已有缓存故事→跳过；没有到期卡→跳过；否则同步生成故事并
  预热 TTS 音频缓存
- 旧版（#420）遍历全部叶子牌组、一律用默认 `mode="story"` 生成——每天产出
  大量没人看的故事，真正用到的聚合牌组反而漏掉，已废弃

只依赖 Python 标准库（`urllib.request`、`base64`、`json` 等），不需要安装
任何依赖，本地（launchd）和服务器（cron，见 issue #417）都可以直接用同一
份脚本。

### 前提

- 服务器（`bash run.sh` 或对应 systemd 服务）必须已经在运行——脚本只发
  HTTP 请求，不会自己启动服务器。
- 服务器串行处理各键（新闻/简报模式可能耗时数分钟，脚本设置了 15 分钟
  超时），单键失败只记录错误、不中断整体；脚本把返回的汇总
  （generated / skipped_cached / skipped_no_due / failed）逐项打印。

### 用法

```bash
# 默认连接本机 8000 端口
python scripts/morning_pregen.py

# 指定服务器地址
BASE_URL=http://127.0.0.1:8001 python scripts/morning_pregen.py

# 如果服务器加了 HTTP Basic 认证（配合认证相关 issue）
AUTH_USERNAME=daniel AUTH_PASSWORD=xxxx python scripts/morning_pregen.py
```

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BASE_URL` | `http://127.0.0.1:8000` | 目标服务器地址 |
| `AUTH_USERNAME` | 无 | HTTP Basic 认证用户名（可选，需和 `AUTH_PASSWORD` 一起设置） |
| `AUTH_PASSWORD` | 无 | HTTP Basic 认证密码（可选） |

退出码：全部成功或没有待处理项时为 `0`；只要有一项失败就是 `1`（方便
launchd/cron 的失败通知）。

---

### macOS：用 launchd 每天早上 06:00 自动运行

launchd 是 macOS 的定时任务机制（比 cron 更适合 Mac，因为它能处理系统
休眠/唤醒）。

1. 创建 plist 文件 `~/Library/LaunchAgents/com.ankiadvanced.morning-pregen.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ankiadvanced.morning-pregen</string>

    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/daniel/Documents/AnkiAdvanced/scripts/morning_pregen.py</string>
    </array>

    <key>EnvironmentVariables</key>
    <dict>
        <key>BASE_URL</key>
        <string>http://127.0.0.1:8000</string>
    </dict>

    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>6</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>

    <key>StandardOutPath</key>
    <string>/Users/daniel/Documents/AnkiAdvanced/data/morning-pregen.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/daniel/Documents/AnkiAdvanced/data/morning-pregen.err.log</string>
</dict>
</plist>
```

（路径按实际用户名/项目路径调整；如果生产服务器加了认证，把
`AUTH_USERNAME`/`AUTH_PASSWORD` 也加进 `EnvironmentVariables`。）

2. 加载并启动：

```bash
launchctl load ~/Library/LaunchAgents/com.ankiadvanced.morning-pregen.plist
```

3. 常用管理命令：

```bash
# 立即手动触发一次（不用等到 06:00）
launchctl start com.ankiadvanced.morning-pregen

# 查看日志
tail -f ~/Documents/AnkiAdvanced/data/morning-pregen.log

# 卸载
launchctl unload ~/Library/LaunchAgents/com.ankiadvanced.morning-pregen.plist
```

---

### Linux 服务器：cron + systemd

服务器上假设 FastAPI 服务由 systemd 管理（见 issue #417 的部署文档），
cron 只负责在服务已经运行时定时触发预生成：

```cron
# 每天 06:00 运行早晨预生成脚本（假设服务已由 systemd 常驻运行）
0 6 * * * cd /opt/ankiadvanced && /usr/bin/python3 scripts/morning_pregen.py >> data/morning-pregen.log 2>&1
```

用 `crontab -e` 添加以上一行。如果服务地址/端口非默认，加上环境变量：

```cron
0 6 * * * cd /opt/ankiadvanced && BASE_URL=http://127.0.0.1:8000 /usr/bin/python3 scripts/morning_pregen.py >> data/morning-pregen.log 2>&1
```

---

## podcast_check.py（issue #479，RSS 源 #497，听悟转录 #498）

对运行中的服务器发一次 `POST /api/podcast/check` 请求：服务器遍历配置的
播客 RSS 源（`podcast_config.feeds`，JSON 数组，默认种子为声动早咖啡 +
声东击西两个 feed）看有没有新单集，对每个新单集转录（见下方转录链）、生成
德语摘要 + HSK5+ 生词表（AI，`ai.resolve_briefing_model()`），并给
`podcast_config.email_to` 发邮件通知。同一单集（RSS item guid，存在
`video_id` 列，唯一约束）不会重复处理。风格与 `morning_pregen.py` 一致：
除转录链的可选依赖外不需要额外安装。

**RSS 源（issue #497，取代已死的 YouTube 频道源）：** YouTube 对服务器
数据中心 IP 强制 bot 验证，Cookie 方案（#491）也很快失效，於是改用播客
官方 RSS 的 MP3 enclosure 直链——没有 bot 墙，不需要 Cookie，标题/日期/
时长都在 feed 里现成。`fetch_new_videos()` 遍历 `podcast_config.feeds`
里的每个 feed URL，用标准库 `xml.etree` 解析：单集唯一 id 用 `<guid>`
（没有则退化用 enclosure URL），标题/发布时间/单集网页链接
（`<link>`，存进 `youtube_url` 列，字段名是历史遗留）、MP3 直链
（`<enclosure url=...>`，存 `audio_url`）、时长（`<itunes:duration>`，
支持"秒"/"MM:SS"/"H:MM:SS"三种格式，解析成 `duration_seconds`）都直接来自
feed。每个 feed **首次**被爬到时（该 feed 在库里一集都没有）只回填最新 3
期；之后的每次爬取只收集"比库里已知的最新一集更新"的单集（feed 按惯例
新到旧排列，扫到第一个已知 guid 就停），避免像声动早咖啡这种有上千期历史
的日更节目在某一轮把几百期旧节目全部当作"新"的塞进来。

单集时长（RSS 自带，不用下载音频就知道）作为**下载前**的护栏：超过 3
小时的单集直接跳过（成本/时间护栏），Whisper 另有独立的
`whisper_max_minutes` 门槛（见下）。

**转录链（issue #498 通义听悟为主力，取代 #486/#485 的 NotebookLM/Whisper
两级链）：**

1. **通义听悟（官方 API，主力，issue #498）**：把 RSS 的 MP3 直链原样提交
   给阿里云通义听悟离线转写接口（`CreateTask`，`type=offline`，
   `Input.FileUrl=<直链>`，`Input.SourceLanguage=cn`）——**不需要下载音频**，
   官方 API，约 ¥0.6/小时（比 Whisper 的约 ¥1.3/小时更便宜），新用户有
   90 天每天 2 小时免费额度。轮询 `GetTaskInfo`（间隔 15 秒，上限 20
   分钟）等 `TaskStatus=COMPLETED`，再从 `Result.Transcription`（一个指向
   JSON 转写结果的 URL）下载并拼接成纯文本。需要环境变量
   `ALIBABA_CLOUD_ACCESS_KEY_ID`/`ALIBABA_CLOUD_ACCESS_KEY_SECRET`（SDK
   标准命名）+ `TINGWU_APP_KEY`（控制台创建应用拿到），任一缺失或调用失败
   都只记日志、落到下一级，不会让爬虫报错（见下方一次性开通步骤）。
2. **Whisper（付费，保底，issue #485）**：听悟未配置/失败时落到这里——
   **但仅当单集时长 ≤ `podcast_config.whisper_max_minutes`（默认 30
   分钟，0=不限制）时才会尝试**，否则直接跳过、记日志（issue #495：早咖啡
   类短节目 10-15 分钟，Daniel 不想为 60-90 分钟的长节目付费）。这一级
   才会真正下载音频（`urllib` 直接拉 RSS 的 MP3 直链，不再用 yt-dlp）→
   `ffmpeg` 转 16kHz 单声道 32kbps 单个 mp3（超过 20 分钟按段切分，
   `-c copy` 不重新编码）→ 逐段调用 OpenAI `gpt-4o-mini-transcribe`（需要
   `OPENAI_API_KEY`）转录后拼接。
3. **NotebookLM（免费但非官方，可选，issue #486）**：Whisper 也失败/被
   门槛跳过时落到这里，复用同一份已下载的 mp3 → 上传到专用笔记本
   "AnkiAdvanced Transcripts"（笔记本 id 缓存进
   `podcast_config.notebooklm_notebook_id`）→ 轮询等索引完成（上限 10
   分钟）→ 读取来源全文（fulltext）作为转录 → 删除该来源（防止笔记本无限
   膨胀）。用的是非官方库 `notebooklm-py`（见下方一次性设置），未认证时
   自动跳过（不报错）。

`transcriber` 可选值：`auto`（默认，依次尝试听悟 → Whisper → NotebookLM）
| `tingwu`（只走听悟）| `whisper`（跳过听悟，只走 Whisper，仍受时长门槛）
| `notebooklm`（只走 NotebookLM）| `off`（整条转录链都不走）。旧键
`whisper_fallback=0` 仍兼容，等价于 `off`。

音频（走 Whisper/NotebookLM 时）用完立即删除，两条路径共用同一份下载+
转码结果，不会重复下载；听悟提交直链完全不下载。**Whisper/NotebookLM 需要
服务器安装 `ffmpeg`**（`apt install ffmpeg`）——缺失时只记警告并跳过这两条
路径（听悟不受影响）。`DISABLE_AI=1`（开发模式）下整条转录链都不会触发，
避免意外调用外部服务。每期转录用了哪条路径（`tingwu`/`whisper`/
`notebooklm`）都会记日志，并存进 `podcast_episodes.transcript_source`。

### 通义听悟一次性开通设置（issue #498）

1. 阿里云控制台开通"通义听悟"服务（新用户 90 天每天 2 小时免费额度）
2. 控制台 [RAM 访问控制] 创建 AccessKey（`AccessKey ID` /
   `AccessKey Secret`），建议用独立的最小权限子账号而非主账号 root key
3. 通义听悟控制台创建一个"应用"，拿到 `AppKey`
4. 把三个值写进服务器的 `.env`（或 systemd 环境文件）：

```bash
ALIBABA_CLOUD_ACCESS_KEY_ID=xxxx
ALIBABA_CLOUD_ACCESS_KEY_SECRET=xxxx
TINGWU_APP_KEY=xxxx
```

未配置这三个变量时 `_transcribe_via_tingwu` 只记 info 日志并返回
`None`（视为"未开通"），整条链自动落到 Whisper/NotebookLM——**不会**让
爬虫报错。失败的单集会被每轮自动重试（7 天内的 error 状态），也可以用
`POST /api/podcast/episodes/{id}/retry` 手动逐集重试。

### NotebookLM 一次性认证设置（issue #486）

NotebookLM 没有公开 API，`notebooklm-py` 用的是非官方的浏览器 Cookie /
master-token 方式，需要在**有浏览器的机器（Daniel 的 Mac）** 上登录一次，
再把凭据文件复制到服务器：

```bash
# 1. 本地装库（含浏览器登录用的 Playwright 支持）
pip install 'notebooklm-py[browser]'

# 2. 本地登录（会弹出浏览器窗口，用 Google 账号登录一次）
notebooklm login

# 3. 认证信息默认存在 ~/.notebooklm/storage_state.json（或
#    ~/.notebooklm/profiles/<profile>/storage_state.json，用了 profile 的话）
#    把这个文件复制到服务器同样的路径（用普通用户权限运行播客爬虫的账号下）：
scp ~/.notebooklm/storage_state.json anki@<server>:~/.notebooklm/storage_state.json

# 4. 服务器上验证凭据可用（不需要浏览器，纯本地校验+可选网络测试）
notebooklm auth check --test
```

服务器无头环境不需要装 `[browser]` extra（`requirements.txt` 里的
`notebooklm-py` 是精简版，浏览器登录只在本地跑一次）。会话过期后
`notebooklm auth refresh` 可自愈（刷新 CSRF/session token，不需要重新走浏览器
登录），**建议服务器 cron 里定期跑一次**：

```cron
# 每天凌晨刷新一次 NotebookLM 会话，防止过期
0 3 * * * NOTEBOOKLM_HOME=/home/anki/.notebooklm /usr/local/bin/notebooklm auth refresh --quiet >> /home/anki/AnkiAdvanced/data/notebooklm-refresh.log 2>&1
```

凭据文件不存在或加载失败时，`_transcribe_via_notebooklm` 只记 info 日志并
返回 `None`（视为"未认证"）——NotebookLM 是转录链最后一级，失败即
`no_transcript`——**不会**让爬虫报错。

用法与环境变量同 `morning_pregen.py`（`BASE_URL`/`AUTH_USERNAME`/`AUTH_PASSWORD`）。
另外邮件发送需要 SMTP 环境变量（`SMTP_HOST`/`SMTP_PORT`/`SMTP_USERNAME`/
`SMTP_PASSWORD`/`SMTP_FROM`/`PUBLIC_BASE_URL`，见 CLAUDE.md 环境变量表）——
未配置时服务器只是跳过发信并记日志，不算失败。

```bash
python scripts/podcast_check.py
```

### 服务器 cron：每小时检查一次

```cron
0 * * * * cd /opt/ankiadvanced && /usr/bin/python3 scripts/podcast_check.py >> data/podcast-check.log 2>&1
```
