#!/bin/bash
# 离线模式同步脚本（议题 #612）
#
#   bash sync_offline.sh pull    上飞机前：把服务器的数据库和音频拉到笔记本
#   bash sync_offline.sh push    落地后：把飞机上的复习结果合并回服务器
#
# 服务器永远是主库。push 只合并 cards + review_log 两张表，
# 离线期间服务器 cron 新增的播客单集、预生成故事都不会被覆盖。
set -euo pipefail

SERVER="${ANKI_SERVER:-anki@207.180.204.135}"
REMOTE_DIR="${ANKI_REMOTE_DIR:-/home/anki/AnkiAdvanced}"
REMOTE_DB="$REMOTE_DIR/data/srs.db"
REMOTE_SNAPSHOT="/tmp/anki_offline_snapshot.db"
REMOTE_INCOMING="/tmp/anki_offline_incoming.db"

cd "$(dirname "$0")"
LOCAL_DB="data/offline.db"
SERVER_SCRIPT="scripts/offline_sync_server.py"
MANIFEST="data/.offline_tts_manifest"
# 提前多少天的到期词也一起同步音频——离线期间可以往前学
DAYS_AHEAD="${ANKI_OFFLINE_DAYS_AHEAD:-14}"

ACTION="${1:-}"

# 服务器端脚本通过 stdin 管道执行——不依赖服务器上是否已经部署了这个版本的代码
run_remote() {
    ssh "$SERVER" "python3 - $*" < "$SERVER_SCRIPT"
}

case "$ACTION" in
pull)
    echo "== 步骤 1/5：检查 SSH 连接 =="
    echo "   连接 $SERVER …"
    ssh -o ConnectTimeout=10 "$SERVER" "test -f '$REMOTE_DB'" \
        || { echo "❌ 连不上服务器，或找不到 ${REMOTE_DB}。你现在有网／VPN 吗？"; exit 1; }
    echo "   ✅ 连接正常，生产数据库存在"

    echo
    echo "== 步骤 2/5：在服务器上生成同步令牌并做数据库快照（约 20 秒）=="
    echo "   （令牌保证这份离线库只能被推回一次；快照对正在运行的服务是安全的）"
    echo "   快照会剔除飞机上用不到的大块数据：API 调用日志、播客转录全文、故事提示词"
    run_remote "prepare '$REMOTE_DB' '$REMOTE_SNAPSHOT'"
    echo "   ✅ 快照完成"

    echo
    echo "== 步骤 3/5：下载数据库到 ${LOCAL_DB}（压缩后约 7 MB，几秒钟）=="
    mkdir -p data
    if [ -f "$LOCAL_DB" ]; then
        mv "$LOCAL_DB" "$LOCAL_DB.replaced-$(date +%Y%m%d-%H%M%S)"
        echo "   （上一份离线库已改名备份，没有覆盖）"
    fi
    scp -C "$SERVER:$REMOTE_SNAPSHOT" "$LOCAL_DB"
    ssh "$SERVER" "rm -f '$REMOTE_SNAPSHOT'"
    echo "   ✅ 数据库已下载"

    echo
    echo "== 步骤 4/5：同步会用到的 TTS 音频 =="
    echo "   服务器上攒了 ~120 MB 音频，绝大部分是不到期的旧词。"
    echo "   这里只算出接下来真正会听到的那些文件（故事句子 + 到期词），通常几 MB。"
    mkdir -p data/tts
    PY=.venv/bin/python
    [ -x "$PY" ] || PY=python3
    "$PY" scripts/offline_tts_manifest.py "$LOCAL_DB" --days-ahead "$DAYS_AHEAD" \
        | sort > "$MANIFEST.want"
    echo "   会用到 $(wc -l < "$MANIFEST.want" | tr -d ' ') 段语音"
    # 清单里有不少词服务器上还没生成过音频。先取交集，
    # 否则 rsync 会为每个缺失文件报错并以非零码退出，让整个脚本中断。
    ssh "$SERVER" "ls '$REMOTE_DIR/data/tts'" | sort > "$MANIFEST.have"
    comm -12 "$MANIFEST.want" "$MANIFEST.have" > "$MANIFEST"
    echo "   服务器上实际存在 $(wc -l < "$MANIFEST" | tr -d ' ') 个（其余的词服务器也还没生成过音频）"
    if [ -s "$MANIFEST" ]; then
        # --progress（不是 GNU 专有的 --info=progress2）：macOS 自带的
        # openrsync 不认后者，会直接报错退出（议题 #617）
        rsync -a --progress --files-from="$MANIFEST" \
            "$SERVER:$REMOTE_DIR/data/tts/" data/tts/
    fi
    rm -f "$MANIFEST" "$MANIFEST.want" "$MANIFEST.have"
    echo "   ✅ 音频同步完成"

    echo
    echo "== 步骤 5/5：完成 =="
    echo "   现在可以断网了。飞机上运行："
    echo "       bash run.offline.sh"
    echo "   然后浏览器打开 http://localhost:8001"
    echo
    echo "   落地有网后运行： bash sync_offline.sh push"
    echo
    echo "把以上全部输出发给 Claude。"
    ;;

push)
    echo "== 步骤 1/4：检查本地离线数据库 =="
    [ -f "$LOCAL_DB" ] || { echo "❌ 找不到 ${LOCAL_DB}——是不是还没 pull，或者已经 push 过了？"; exit 1; }
    echo "   ✅ 找到 ${LOCAL_DB}（$(du -h "$LOCAL_DB" | cut -f1)）"
    echo "   提示：如果离线服务还开着，先按 Ctrl+C 停掉，确保数据全部写盘"

    echo
    echo "== 步骤 2/4：上传到服务器（压缩后约 7 MB）=="
    scp -C "$LOCAL_DB" "$SERVER:$REMOTE_INCOMING"
    echo "   ✅ 上传完成"

    echo
    echo "== 步骤 3/4：在服务器上合并复习结果 =="
    echo "   （只合并 cards + review_log；令牌不匹配会直接拒绝，防止重复合并）"
    run_remote "merge '$REMOTE_DB' '$REMOTE_INCOMING'"
    ssh "$SERVER" "rm -f '$REMOTE_INCOMING'"
    echo "   ✅ 合并完成"

    echo
    echo "== 步骤 4/4：归档本地离线库 =="
    mv "$LOCAL_DB" "$LOCAL_DB.pushed-$(date +%Y%m%d-%H%M%S)"
    echo "   ✅ 已改名归档——下次要离线复习，重新跑一次 pull"
    echo
    echo "把以上全部输出发给 Claude。"
    ;;

*)
    echo "用法： bash sync_offline.sh pull   （上飞机前，需要网络）"
    echo "       bash sync_offline.sh push   （落地后，需要网络）"
    exit 1
    ;;
esac
