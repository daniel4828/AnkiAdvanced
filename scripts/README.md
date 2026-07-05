# scripts/

## morning_pregen.py（issue #420）

新闻 / 简报模式的故事生成很慢（多次串行 AI 调用），Daniel 早上打开页面时
不想等。这个脚本对**运行中的服务器**发请求，提前把今天所有有到期卡片的
（牌组, 类别）的故事生成好，并预热对应的 TTS 音频缓存。

只依赖 Python 标准库（`urllib.request`、`base64`、`json` 等），不需要安装
任何依赖，本地（launchd）和服务器（cron，见 issue #417）都可以直接用同一
份脚本。

### 前提

- 服务器（`bash run.sh` 或对应 systemd 服务）必须已经在运行——脚本只发
  HTTP 请求，不会自己启动服务器。
- 脚本读取 `GET /api/decks`，找出所有**叶子牌组**（没有子牌组的节点）里
  到期数量（`counts.new + learning + review + learning_future`）大于 0、
  且**未被暂停**（`all_suspended` 不为真）的（牌组, 类别）。
  类别键名固定为 `listening`（听力）/ `reading`（阅读）/ `creating`（写作），
  与 `routes/decks.py` 中的 `VALID_CATEGORIES` 一致。
- 对每一项：`GET /api/story/{deck_id}/{category}`（已有今天的缓存故事则
  秒回，否则同步生成——新闻/简报模式可能耗时数分钟，脚本设置了 15 分钟
  超时），成功后再 `POST /api/preload-session/{deck_id}/{category}` 预热
  TTS 音频缓存。
- 串行执行（一次只处理一个），单项失败只记录错误、不中断整体，最后打印
  成功/失败/跳过（已暂停）数量的汇总。

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
