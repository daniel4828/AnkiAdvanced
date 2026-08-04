#!/bin/bash
# 本地模式启动（议题 #625）——笔记本上的日常实例，像 Anki 桌面版。
#
# 和 run.offline.sh 的区别：这个模式**有网就全功能**（AI 故事、edge-tts 生成、
# 翻译都能用），断网后自动降级成只读缓存，不需要重启。
# 飞机上请改用 run.offline.sh——那个模式连探测都不做，保证零出站连接。
#
# 数据库和离线模式共用 data/offline.db，所以在家同步好就能直接带上飞机。
# 同步不再需要命令行：界面顶栏点同步按钮即可（也可以 bash sync_offline.sh sync）。
set -e
cd "$(dirname "$0")"

if [ ! -f data/offline.db ]; then
    echo "❌ 找不到 data/offline.db —— 还没从服务器同步过。"
    echo "   先运行： bash sync_offline.sh sync"
    exit 1
fi

# 需要 .env：本地模式要用 AI 密钥。但不加载 AUTH_*——本机 localhost 不需要 Basic Auth。
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi
unset AUTH_USERNAME AUTH_PASSWORD

PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "💻 本地模式启动中…"
echo "    数据库： data/offline.db"
echo "    地址：   http://localhost:8001"
echo "    （有网时 AI 和语音生成全部可用；断网后自动降级为只读缓存）"
echo

LOCAL_MODE=1 DB_PATH=data/offline.db PORT=8001 "$PY" main.py
