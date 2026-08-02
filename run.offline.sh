#!/bin/bash
# 离线模式启动（议题 #612）——飞机上用，全程不联网。
#
# 前提：起飞前已经跑过 `bash sync_offline.sh pull`。
# 落地后跑 `bash sync_offline.sh push` 把复习结果同步回服务器。
set -e
cd "$(dirname "$0")"

if [ ! -f data/offline.db ]; then
    echo "❌ 找不到 data/offline.db"
    echo "   有网的时候先运行： bash sync_offline.sh pull"
    exit 1
fi

# .env 只为读取 DB 无关的配置；离线模式下所有 API 密钥都用不上。
# 不加载 AUTH_* —— 本机 localhost 不需要 Basic Auth。
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3

echo "✈️  离线模式启动中…"
echo "    数据库： data/offline.db"
echo "    地址：   http://localhost:8001"
echo "    （AI、新闻抓取、TTS 生成全部关闭；音频只读 data/tts/ 缓存）"
echo

OFFLINE_MODE=1 DB_PATH=data/offline.db PORT=8001 "$PY" main.py
