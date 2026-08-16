#!/bin/bash
# 笔记本 ↔ 服务器同步脚本（议题 #612、#625）
#
#   bash sync_offline.sh sync    日常：一步搞定（先推回复习结果，再拉最新的库）
#   bash sync_offline.sh pull    只拉不推（第一次用，或者本地库已经推过了）
#   bash sync_offline.sh push    只推不拉，推完把本地库归档（上飞机那套老流程）
#
# 界面顶栏的同步按钮跑的就是 `sync`（见 routes/sync.py）。
#
# 服务器永远是主库。push/sync 只合并 cards + review_log 两张表，
# 服务器上新增的播客单集、预生成故事都不会被覆盖。同一张卡两边都复习过时，
# 按 last_review 谁晚谁赢（#625）。
set -euo pipefail

SERVER="${ANKI_SERVER:-anki@207.180.204.135}"
REMOTE_DIR="${ANKI_REMOTE_DIR:-/home/anki/biangbiangmian3000}"
REMOTE_DB="$REMOTE_DIR/data/srs.db"
REMOTE_SNAPSHOT="/tmp/anki_offline_snapshot.db"
REMOTE_INCOMING="/tmp/anki_offline_incoming.db"

cd "$(dirname "$0")"
# 覆盖它就能拿一份临时库对着服务器上的副本安全演练，不动真的 data/offline.db
LOCAL_DB="${ANKI_LOCAL_DB:-data/offline.db}"
SERVER_SCRIPT="scripts/offline_sync_server.py"
MANIFEST="data/.offline_tts_manifest"
# 提前多少天的到期词也一起同步音频——离线期间可以往前学
DAYS_AHEAD="${ANKI_OFFLINE_DAYS_AHEAD:-14}"

# rsync 的 --progress 会刷一堆回车，从界面按钮跑的时候只是噪音。
# 只有在真正的终端里才开（脚本被 routes/sync.py 用管道调用时 stdout 不是 tty）。
RSYNC_OPTS=(-a)
[ -t 1 ] && RSYNC_OPTS+=(--progress)

ACTION="${1:-}"

# 服务器端脚本通过 stdin 管道执行——不依赖服务器上是否已经部署了这个版本的代码
run_remote() {
    ssh "$SERVER" "python3 - $*" < "$SERVER_SCRIPT"
}

check_ssh() {
    echo "   连接 $SERVER …"
    ssh -o ConnectTimeout=10 "$SERVER" "test -f '$REMOTE_DB'" \
        || { echo "❌ 连不上服务器，或找不到 ${REMOTE_DB}。你现在有网／VPN 吗？"; exit 1; }
    echo "   ✅ 连接正常，生产数据库存在"
}

# 把本地库推上去合并。合并失败（比如令牌不匹配）会因为 set -e 中断整个流程——
# 这是故意的：绝不能在没推成功的情况下继续 pull，那会直接覆盖掉本地的复习记录。
do_merge() {
    echo "   上传 ${LOCAL_DB}（压缩后约 7 MB）…"
    scp -C "$LOCAL_DB" "$SERVER:$REMOTE_INCOMING"
    echo "   在服务器上合并（只动 cards + review_log；令牌不匹配会直接拒绝）…"
    run_remote "merge '$REMOTE_DB' '$REMOTE_INCOMING'"
    ssh "$SERVER" "rm -f '$REMOTE_INCOMING'"
    echo "   ✅ 合并完成"
}

do_snapshot_and_download() {
    echo "   在服务器上生成同步令牌并做快照（约 20 秒）…"
    echo "   （令牌保证这份库只能被推回一次；快照对正在运行的服务是安全的）"
    echo "   快照会剔除用不到的大块数据：API 调用日志、播客转录全文、故事提示词"
    run_remote "prepare '$REMOTE_DB' '$REMOTE_SNAPSHOT'"

    mkdir -p data
    echo "   下载到 ${LOCAL_DB}（压缩后约 7 MB，几秒钟）…"
    # 先落到 .incoming 再 mv 就位：应用可能正开着，直接 scp 覆盖会让它在
    # 好几秒里读到一个写了一半的库。rename 是原子的，窗口缩到几乎为零（#625）。
    scp -C "$SERVER:$REMOTE_SNAPSHOT" "$LOCAL_DB.incoming"
    ssh "$SERVER" "rm -f '$REMOTE_SNAPSHOT'"
    if [ -f "$LOCAL_DB" ]; then
        mv "$LOCAL_DB" "$LOCAL_DB.replaced-$(date +%Y%m%d-%H%M%S)"
        echo "   （上一份本地库已改名备份，没有删除）"
    fi
    mv "$LOCAL_DB.incoming" "$LOCAL_DB"
    # 旧库的日志文件套到新库上会直接损坏数据。现在用的是默认 rollback journal，
    # 不该有这些文件，但删一下不花钱。
    rm -f "$LOCAL_DB-wal" "$LOCAL_DB-shm" "$LOCAL_DB-journal"
    echo "   ✅ 数据库已就位"
}

do_tts() {
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
        # 不能用 GNU 专有的 --info=progress2：macOS 自带的 openrsync 不认（议题 #617）
        rsync "${RSYNC_OPTS[@]}" --files-from="$MANIFEST" \
            "$SERVER:$REMOTE_DIR/data/tts/" data/tts/
    fi
    rm -f "$MANIFEST" "$MANIFEST.want" "$MANIFEST.have"
    echo "   ✅ 音频同步完成"
}

case "$ACTION" in
sync)
    echo "== 步骤 1/4：检查 SSH 连接 =="
    check_ssh

    echo
    if [ -f "$LOCAL_DB" ]; then
        echo "== 步骤 2/4：把本地的复习结果推回服务器 =="
        do_merge
    else
        echo "== 步骤 2/4：跳过推送 =="
        echo "   本地还没有 ${LOCAL_DB}——这是第一次同步，只需要下载。"
    fi

    echo
    echo "== 步骤 3/4：拉取服务器最新数据库 =="
    do_snapshot_and_download

    echo
    echo "== 步骤 4/4：同步会用到的 TTS 音频 =="
    do_tts

    echo
    echo "🎉 同步完成。"
    ;;

pull)
    echo "== 步骤 1/3：检查 SSH 连接 =="
    check_ssh
    echo
    echo "== 步骤 2/3：快照 + 下载 =="
    echo "   ⚠️  只拉不推：本地库里还没同步的复习记录会被覆盖。"
    echo "      想保住它们请改用： bash sync_offline.sh sync"
    do_snapshot_and_download
    echo
    echo "== 步骤 3/3：同步会用到的 TTS 音频 =="
    do_tts
    echo
    echo "   现在可以断网了。飞机上运行 bash run.offline.sh，"
    echo "   平时（有网全功能）运行 bash run.local.sh，都是 http://localhost:8001"
    echo
    echo "把以上全部输出发给 Claude。"
    ;;

push)
    echo "== 步骤 1/3：检查本地数据库 =="
    [ -f "$LOCAL_DB" ] || { echo "❌ 找不到 ${LOCAL_DB}——是不是还没同步过，或者已经 push 过了？"; exit 1; }
    echo "   ✅ 找到 ${LOCAL_DB}（$(du -h "$LOCAL_DB" | cut -f1)）"
    echo "   提示：如果本地服务还开着，先按 Ctrl+C 停掉，确保数据全部写盘"

    echo
    echo "== 步骤 2/3：上传并合并 =="
    do_merge

    echo
    echo "== 步骤 3/3：归档本地库 =="
    mv "$LOCAL_DB" "$LOCAL_DB.pushed-$(date +%Y%m%d-%H%M%S)"
    echo "   ✅ 已改名归档——下次要在笔记本上复习，重新跑一次 sync"
    echo
    echo "把以上全部输出发给 Claude。"
    ;;

*)
    echo "用法： bash sync_offline.sh sync   （日常：先推后拉，一步到位）"
    echo "       bash sync_offline.sh pull   （只拉不推）"
    echo "       bash sync_offline.sh push   （只推不拉，推完归档本地库）"
    exit 1
    ;;
esac
