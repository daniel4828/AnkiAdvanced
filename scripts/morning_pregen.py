#!/usr/bin/env python3
"""早晨自动预生成脚本（issue #420，issue #458 重构）。

对一台运行中的服务器发送一次 POST /api/pregen-today 请求。该端点在服务器端
找到"最近一天（今天除外，最多回看14天）"真正生成过故事的所有 (deck_id,
category, lang) 键——也就是 Daniel 昨天实际复习用到的牌组/类别/模式组合
（包括 briefing/news 等聚合牌组模式），而不是像旧版那样遍历全部叶子牌组、
一律用默认 mode="story" 生成——那样会生成大量没人看的故事，真正用到的
聚合牌组反而漏掉。

服务器对每个键：今天已有缓存故事则跳过；没有到期卡片则跳过；否则用该键
上次的生成参数（mode/topic/grammar 等；news/briefing 的 articles 会被丢弃，
让它们重新抓取当天新闻）同步生成故事并预热 TTS 音频缓存，这样 Daniel 早上
打开页面时故事和语音都已经就绪。

只使用 Python 标准库（urllib.request 等），本地用 launchd、服务器用
cron 都可以直接运行，见 scripts/README.md。

用法：
    BASE_URL=http://127.0.0.1:8000 python scripts/morning_pregen.py

环境变量：
    BASE_URL       服务器地址，默认 http://127.0.0.1:8000
    AUTH_USERNAME  可选，HTTP Basic 认证用户名（配合认证议题）
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

# /api/pregen-today 对每个键都可能同步生成故事——新闻/简报模式可能涉及多次
# 串行 AI 调用，加上多个键顺序处理，耗时可达数分钟到十几分钟，因此设置较长
# 的超时。
STORY_TIMEOUT_SECONDS = 15 * 60


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def _auth_header() -> dict:
    if AUTH_USERNAME is not None and AUTH_PASSWORD is not None:
        token = base64.b64encode(f"{AUTH_USERNAME}:{AUTH_PASSWORD}".encode()).decode()
        return {"Authorization": f"Basic {token}"}
    return {}


def _request(method: str, path: str, timeout: int, body: dict | None = None):
    """Send an HTTP request to BASE_URL + path, return parsed JSON (or None)."""
    url = f"{BASE_URL}{path}"
    data = None
    headers = _auth_header()
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw:
            return None
        return json.loads(raw)


def main() -> int:
    _log(f"早晨预生成开始  BASE_URL={BASE_URL}")

    try:
        summary = _request("POST", "/api/pregen-today", STORY_TIMEOUT_SECONDS)
    except urllib.error.URLError as e:
        _log(f"无法连接服务器 {BASE_URL}: {e}")
        return 1
    except Exception as e:
        _log(f"预生成请求失败: {e}")
        return 1

    if not summary:
        _log("服务器没有返回汇总结果（响应为空），视为失败。")
        return 1

    keys = summary.get("keys", 0)
    generated = summary.get("generated", [])
    skipped_cached = summary.get("skipped_cached", [])
    skipped_no_due = summary.get("skipped_no_due", [])
    failed = summary.get("failed", [])

    _log(f"日期={summary.get('date')}  候选键={keys}")

    for label in generated:
        _log(f"  ✓ 已生成 {label}")
    for label in skipped_cached:
        _log(f"  - 跳过（今天已有故事） {label}")
    for label in skipped_no_due:
        _log(f"  - 跳过（无到期卡片，或 AI 已禁用） {label}")
    for item in failed:
        _log(f"  ✗ 失败 {item.get('key')}: {item.get('error')}")

    _log("---- 汇总 ----")
    _log(f"候选键: {keys}  已生成: {len(generated)}  跳过(已缓存): {len(skipped_cached)}  "
         f"跳过(无到期): {len(skipped_no_due)}  失败: {len(failed)}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
