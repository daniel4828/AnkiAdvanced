#!/usr/bin/env python3
"""早晨自动预生成脚本（issue #420）。

对一台运行中的服务器发请求，提前生成"今天"所有有到期卡片的叶子牌组
（牌组+类别）的故事，并预热对应的 TTS 音频缓存，这样 Daniel 早上打开
页面时，故事和语音都已经就绪（新闻/简报模式尤其慢，多次串行 AI 调用）。

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

# /api/story/{deck_id}/{category} 在没有缓存故事时会同步生成——新闻/简报模式
# 可能涉及多次串行 AI 调用，耗时可达数分钟，因此设置较长的超时。
STORY_TIMEOUT_SECONDS = 15 * 60
# 牌组列表、预热 TTS 等轻量请求的超时。
SHORT_TIMEOUT_SECONDS = 60

# 叶子牌组分类键名（阅读=reading，听力=listening，写作=creating）——
# 与 routes/decks.py 的 VALID_CATEGORIES / _leaf_pairs 保持一致。
CATEGORIES = ("listening", "reading", "creating")


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


def _flatten(tree: list) -> list:
    result = []
    for node in tree:
        result.append(node)
        result.extend(_flatten(node.get("children") or []))
    return result


def _due_count(deck: dict) -> int:
    counts = deck.get("counts") or {}
    return sum(counts.get(k, 0) for k in ("new", "learning", "review", "learning_future"))


def find_due_leaf_pairs() -> tuple[list[tuple[int, str, str]], int]:
    """Return ([(deck_id, category, deck_name), ...], n_skipped_suspended) for
    leaf decks with due cards in a non-suspended category. Reads GET /api/decks
    (see routes/decks.py:_leaf_pairs / _attach_counts for the tree shape)."""
    tree = _request("GET", "/api/decks", SHORT_TIMEOUT_SECONDS)
    if not tree:
        return [], 0
    pairs = []
    n_skipped = 0
    for deck in _flatten(tree):
        if deck.get("children"):
            continue  # only leaf decks carry a category
        category = deck.get("category")
        if category not in CATEGORIES:
            continue
        if deck.get("all_suspended"):
            _log(f"跳过 已暂停  牌组={deck.get('name')!r} 类别={category}")
            n_skipped += 1
            continue
        if _due_count(deck) <= 0:
            continue
        pairs.append((deck["id"], category, deck.get("name", f"deck#{deck['id']}")))
    return pairs, n_skipped


def pregen_one(deck_id: int, category: str, name: str) -> tuple[bool, str]:
    """Generate (or reuse cached) story for one (deck, category), then preload
    TTS. Returns (success, message)."""
    label = f"牌组={name!r} 类别={category}"
    try:
        story = _request("GET", f"/api/story/{deck_id}/{category}", STORY_TIMEOUT_SECONDS)
    except urllib.error.URLError as e:
        return False, f"{label} 故事生成请求失败: {e}"
    except Exception as e:
        return False, f"{label} 故事生成异常: {e}"

    if isinstance(story, dict) and story.get("error"):
        return False, f"{label} 故事生成返回错误: {story.get('reason')}"
    if not story:
        return False, f"{label} 没有返回故事（可能没有到期卡片或 AI 已禁用）"

    n_sentences = len(story.get("sentences") or [])
    _log(f"  {label} 故事就绪（{n_sentences} 句），开始预热语音…")

    try:
        _request("POST", f"/api/preload-session/{deck_id}/{category}", STORY_TIMEOUT_SECONDS)
    except Exception as e:
        # 故事已经生成成功——语音预热失败不算整体失败，但要记录。
        return True, f"{label} 故事已生成，但语音预热失败: {e}"

    return True, f"{label} 完成（{n_sentences} 句 + 语音预热）"


def main() -> int:
    _log(f"早晨预生成开始  BASE_URL={BASE_URL}")

    try:
        pairs, n_skipped = find_due_leaf_pairs()
    except urllib.error.URLError as e:
        _log(f"无法连接服务器 {BASE_URL}: {e}")
        return 1
    except Exception as e:
        _log(f"读取牌组列表失败: {e}")
        return 1

    if not pairs:
        _log(f"没有找到任何有到期卡片的（牌组, 类别）（跳过已暂停 {n_skipped} 个），无需生成，退出。")
        return 0

    _log(f"共 {len(pairs)} 个（牌组, 类别）待处理:")
    for deck_id, category, name in pairs:
        _log(f"  - {name!r} / {category} (deck_id={deck_id})")

    n_ok = 0
    n_fail = 0
    failures: list[str] = []

    for deck_id, category, name in pairs:
        _log(f"处理中 牌组={name!r} 类别={category} (deck_id={deck_id})…")
        ok, msg = pregen_one(deck_id, category, name)
        if ok:
            n_ok += 1
            _log(f"  ✓ {msg}")
        else:
            n_fail += 1
            failures.append(msg)
            _log(f"  ✗ {msg}")

    _log("---- 汇总 ----")
    _log(f"成功: {n_ok}  失败: {n_fail}  跳过(已暂停): {n_skipped}  总计处理: {len(pairs)}")
    if failures:
        _log("失败详情:")
        for f in failures:
            _log(f"  - {f}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
