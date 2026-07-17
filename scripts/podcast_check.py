#!/usr/bin/env python3
"""播客爬虫定时脚本（issue #479）。

对一台运行中的服务器发送一次 POST /api/podcast/check 请求——服务器端检查
配置的 YouTube 频道（默认 @shengfm）是否有新视频，下载中文转录、生成德语
摘要 + HSK5+ 生词表，并给 Daniel 发邮件通知。风格照抄
scripts/morning_pregen.py：只用 Python 标准库，本地/服务器都能直接运行。

用法：
    BASE_URL=http://127.0.0.1:8000 python scripts/podcast_check.py

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

# 转录下载 + AI 摘要可能对多个新视频串行处理，耗时可达数分钟，超时设长一点。
CHECK_TIMEOUT_SECONDS = 10 * 60


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _auth_header() -> dict:
    if AUTH_USERNAME is not None and AUTH_PASSWORD is not None:
        token = base64.b64encode(f"{AUTH_USERNAME}:{AUTH_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


def main() -> int:
    _log(f"播客检查开始  BASE_URL={BASE_URL}")

    url = f"{BASE_URL}/api/podcast/check"
    req = urllib.request.Request(url, data=b"", headers=_auth_header(), method="POST")
    try:
        with urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_SECONDS) as resp:
            summary = json.loads(resp.read() or b"{}")
    except urllib.error.URLError as e:
        _log(f"无法连接服务器 {BASE_URL}: {e}")
        return 1
    except Exception as e:
        _log(f"播客检查请求失败: {e}")
        return 1

    if summary.get("skipped"):
        # 服务器用 reason 区分两种跳过（#565）；老服务器没有 reason 字段，
        # 按原来的"已禁用"提示处理。
        if summary.get("reason") == "busy":
            held = summary.get("held_minutes")
            extra = f"（已运行 {held:.0f} 分钟）" if isinstance(held, (int, float)) else ""
            _log(f"上一轮播客检查仍在进行{extra}，本轮跳过。")
        else:
            _log("播客爬虫已在设置里禁用，跳过。")
        return 0

    new = summary.get("new", 0)
    summarized = summary.get("summarized", 0)
    emailed = summary.get("emailed", 0)
    failed = summary.get("failed", 0)

    _log(f"新视频: {new}  已摘要: {summarized}  已发邮件: {emailed}  失败: {failed}")
    if summary.get("error"):
        _log(f"错误: {summary['error']}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
