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

## podcast_check.py（issue #479）

对运行中的服务器发一次 `POST /api/podcast/check` 请求：服务器检查配置的
YouTube 频道（`podcast_config.channel_url`，默认 `@shengfm`）有没有新视频，
对每个新视频下载中文转录（`yt-dlp`，只拿字幕元数据，不下载音视频）、生成
德语摘要 + HSK5+ 生词表（AI，`ai.resolve_briefing_model()`），并给
`podcast_config.email_to` 发邮件通知。同一视频（`video_id` 唯一约束）不会
重复处理。风格与 `morning_pregen.py` 一致：纯标准库，不需要安装依赖（服务
器端需要 `yt-dlp`，已在 `requirements.txt` 里）。

**无字幕时的转录链（issue #486，取代 #485 的纯 Whisper 回退）：** `@shengfm`
频道没有任何字幕（人工/自动都关闭），所以字幕下载总是失败。字幕缺失时按
`podcast_config.transcriber`（默认 `auto`）走：

1. **NotebookLM（免费，主力）**：`yt-dlp` 下载最低码率音频到临时目录 →
   `ffmpeg` 转 16kHz 单声道 32kbps 单个 mp3 → 上传到专用笔记本
   "AnkiAdvanced Transcripts"（笔记本 id 缓存进
   `podcast_config.notebooklm_notebook_id`）→ 轮询等索引完成（上限 10
   分钟）→ 读取来源全文（fulltext）作为转录 → 删除该来源（防止笔记本无限
   膨胀）。用的是非官方库 `notebooklm-py`（见下方一次性设置）。
2. **Whisper（付费，保底，issue #485）**：NotebookLM 未安装/未认证/失败时
   落到这里——**但仅当单集时长 ≤ `podcast_config.whisper_max_minutes`
   （默认 30 分钟，0=不限制）时才会尝试**，否则直接跳过、记日志（issue
   #495：早咖啡类短节目 10-15 分钟，Daniel 不想为 60-90 分钟的长节目付费；
   旧的 whisper_title_filter 因真实标题从不含"早咖啡"而废弃）。复用同一份
   已下载的 mp3（超过 20 分钟按段切分，`-c copy` 不重新编码）→ 逐段调用
   OpenAI `gpt-4o-mini-transcribe`（需要 `OPENAI_API_KEY`）转录后拼接。

`transcriber` 可选值：`auto`（默认，NotebookLM 优先失败落 Whisper）|
`notebooklm`（只走 NotebookLM，不落 Whisper）| `whisper`（跳过 NotebookLM，
只走 Whisper，仍受标题过滤）| `off`（两条都不走，纯字幕模式）。旧键
`whisper_fallback=0` 仍兼容，等价于 `off`。

音频（无论走哪条路径）用完立即删除；单集超过 3 小时会被成本护栏跳过，两条
路径共用同一份下载+转码结果，不会重复下载。**服务器需要安装 `ffmpeg`**
（`apt install ffmpeg`）——缺失时只记警告并跳过整条音频转录链，不会崩溃。
`DISABLE_AI=1`（开发模式）下两条路径都不会触发，避免意外调用外部服务。
每期转录用了哪条路径（`captions`/`notebooklm`/`whisper`）都会记日志，并存进
`podcast_episodes.transcript_source`，方便观察 NotebookLM 何时静默失效。

### YouTube Cookie（issue #491，服务器必需）

YouTube 对数据中心 IP 强制"Sign in to confirm you're not a bot"验证——服务器
上**所有** yt-dlp 调用（元数据、字幕、音频）都会因此失败，必须提供一份登录
浏览器导出的 Cookie 文件。路径取环境变量 `YT_DLP_COOKIES`（默认
`data/yt_cookies.txt`，相对仓库根目录）；文件不存在时自动跳过（本地住宅
IP 开发不需要）。

在 Daniel 的 Mac 上导出并上传（Chrome 需已登录 YouTube；Safari 把
`chrome` 换成 `safari`）：

```bash
.venv/bin/python -m yt_dlp --cookies-from-browser chrome --cookies yt_cookies.txt \
  --skip-download "https://www.youtube.com/watch?v=jNQXAC9IVRw"
scp yt_cookies.txt root@207.180.204.135:/home/anki/AnkiAdvanced/data/yt_cookies.txt
ssh root@207.180.204.135 'chown anki:anki /home/anki/AnkiAdvanced/data/yt_cookies.txt && chmod 600 /home/anki/AnkiAdvanced/data/yt_cookies.txt'
rm yt_cookies.txt
```

Cookie 会过期：当 `data/podcast-check.log` 里再次出现 bot 错误时，重复以上
步骤即可。失败的单集会被每轮自动重试（7 天内的 error 状态），也可以用
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
返回 `None`（视为"未认证"），整条链自动落到 Whisper（如标题匹配过滤器）或
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
