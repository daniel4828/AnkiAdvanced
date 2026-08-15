#!/usr/bin/env python3
"""知识库 Signal 收件定时脚本（issue #749）。

轮询服务器上关联的 signal-cli 设备（`signal -a $SIGNAL_ACCOUNT -o json
receive`），把 Daniel 发给自己「Note to Self」的消息里的 URL 逐个送进
`knowledge.ingest.ingest_url()`——和界面「粘贴 URL」用的是同一条管线
（见 `knowledge/ingest.py`、`knowledge/signal_inbox.py` 的模块说明）。新
入库的链接接着同步跑一遍转录+摘要（`podcast.retry_episode()`），完成后
通过 Signal 把结果发回 Daniel 自己（收到确认 + 每条一行简短结果；已有
完整摘要通知走 `podcast.send_signal()`，这里不重复发）。

和 `scripts/knowledge_mail_check.py` 是同一套设计（不复制第二份实现，
入库都走 `knowledge.ingest.ingest_url()`），只是收件通道不同：IMAP 邮箱
可以把处理失败的邮件留成 UNSEEN 下轮重试，但 `signal-cli receive` 一次
就会把消息从 Signal 服务器上取走，没有"留着不读、下次再看"这个选项。
所以失败的 URL 由 `knowledge/signal_inbox.py` 自己存进一个小的 JSON 重
试队列（`app_settings['signal_retry_queue']`），最多重试 3 次后放弃并
在回执里告知。

风格照抄 `scripts/knowledge_mail_check.py`：直接 `import database` +
`database.init_db()` 后在本进程里完成，不经过任何 HTTP 接口；用同一种
PID 锁文件方式防止 cron 叠跑（处理链接可能触发文章抓取/YouTube 转录/AI
摘要，慢的话能到几分钟到十几分钟）。

用法：
    python scripts/signal_check.py

环境变量（见 CLAUDE.md 环境变量表）：
    SIGNAL_ACCOUNT      关联设备所属的 Signal 号码（如 +49…）；未配置则
                        整个检查被跳过
    SIGNAL_CLI_PATH     signal-cli 可执行文件路径，默认 `signal-cli`
    DB_PATH             数据库路径，默认 data/srs.db

signal-cli 的会话状态（关联设备凭据）存在 `~/.local/share/signal-cli`，
丢了要重新扫码关联——务必把这个目录纳入服务器备份。一次性关联步骤见
`scripts/README.md`。
"""
import fcntl
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", ".signal_check.lock")


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def main() -> int:
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    lock_file = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _log("上一轮 Signal 收件检查仍在进行，本轮跳过。")
        return 0

    try:
        lock_file.write(str(os.getpid()))
        lock_file.flush()

        import database
        database.init_db()
        import knowledge.signal_inbox

        _log("Signal 收件检查开始")
        summary = knowledge.signal_inbox.check_signal_inbox()

        reason = summary.get("reason")
        if reason == "no_account":
            _log("SIGNAL_ACCOUNT 未配置，跳过。")
            return 0
        if reason == "receive_failed":
            _log("signal-cli receive 失败。")
            for err in summary.get("errors", []):
                _log(f"错误: {err}")
            return 1

        _log(
            f"收到消息: {summary['checked']}  已处理: {summary['processed']}  "
            f"跳过: {summary['skipped']}  失败: {summary['failed']}  "
            f"入库 URL 数: {summary['ingested']}"
        )
        for err in summary.get("errors", []):
            _log(f"错误: {err}")

        return 0 if not summary["failed"] else 1
    except Exception as e:
        _log(f"Signal 收件检查异常: {e}")
        return 1
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
