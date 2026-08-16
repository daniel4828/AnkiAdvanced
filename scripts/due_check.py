#!/usr/bin/env python3
"""复习收尾提醒定时脚本（议题 #701）。

对一台运行中的服务器发送一次 POST /api/review/due-notify-check —— 服务器判断
「今天评过 Again 的卡片是否已经全部重新到期」，是则发一封提醒邮件（每个 Anki
日最多一封）。风格照抄 scripts/podcast_check.py：只用标准库，本地/服务器都能跑。

用法：
    BASE_URL=http://127.0.0.1:8000 python scripts/due_check.py

服务器 cron（每 5 分钟一次；条件不满足时脚本什么都不做，很便宜）：
    */5 * * * * cd /home/anki/biangbiangmian3000 && /home/anki/biangbiangmian3000/venv/bin/python \
        scripts/due_check.py >> /home/anki/biangbiangmian3000/data/due_check.log 2>&1

环境变量：
    BASE_URL       服务器地址，默认 http://127.0.0.1:8000
    AUTH_USERNAME  可选，HTTP Basic 认证用户名
    AUTH_PASSWORD  可选，HTTP Basic 认证密码
"""

import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
AUTH_USERNAME = os.environ.get("AUTH_USERNAME")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD")

# 判定是几条 SQL + 可能一次 SMTP 发信，30 秒足够。
CHECK_TIMEOUT_SECONDS = 30


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _auth_header() -> dict:
    if AUTH_USERNAME is not None and AUTH_PASSWORD is not None:
        token = base64.b64encode(f"{AUTH_USERNAME}:{AUTH_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


def main() -> int:
    url = f"{BASE_URL}/api/review/due-notify-check"
    req = urllib.request.Request(url, data=b"", headers=_auth_header(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_SECONDS) as resp:
            result = json.loads(resp.read() or b"{}")
    except urllib.error.URLError as e:
        _log(f"无法连接服务器 {BASE_URL}: {e}")
        return 1
    except Exception as e:
        _log(f"复习提醒检查请求失败: {e}")
        return 1

    # 绝大多数轮次都是"条件未满足"，只在真发了信时才写一行日志，免得
    # 每 5 分钟往日志里灌一条毫无信息量的记录。
    if result.get("sent"):
        _log(f"已发送复习提醒：{result.get('due_now')} 张卡片到期")
    return 0


if __name__ == "__main__":
    sys.exit(main())
