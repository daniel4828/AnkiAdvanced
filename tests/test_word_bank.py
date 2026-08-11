"""Word bank (Reconstruct the sentence) target-word placement — #699.

`buildWordBankOrder()` lives in static/app.js and has no build step, so the
test extracts that one pure function from the source and runs it in node.
Skipped when node isn't installed (it is on ubuntu-latest, where CI runs).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"
FN = "function buildWordBankOrder("

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _extract_fn(source: str, header: str) -> str:
    """Return the full text of a top-level function, by brace matching."""
    start = source.index(header)
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"unbalanced braces after {header}")


def build_order(zh, tokens, target):
    fn = _extract_fn(APP_JS.read_text(encoding="utf-8"), FN)
    script = (
        fn
        + "\nconst [zh, tokens, target] = JSON.parse(process.argv[1]);"
        + "\nprocess.stdout.write(JSON.stringify(buildWordBankOrder(zh, tokens, target)));"
    )
    out = subprocess.run(
        ["node", "-e", script, json.dumps([zh, tokens, target])],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)


def rejoined(order):
    """The order must always reproduce the sentence exactly."""
    return "".join(it.get("char") or it.get("word") for it in order)


def targets(order):
    return [it["word"] for it in order if it["type"] == "target"]


def toks(*texts):
    return [[t, None] for t in texts]


def test_target_across_token_boundary():
    """#699: jieba cuts 活下 into 中活/下来 — offsets must still find it."""
    zh = "TÜV在严格监管中活下来。"
    tokens = toks("T", "Ü", "V", "在", "严格", "监管", "中活", "下来", "。")
    order = build_order(zh, tokens, "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh


def test_target_as_suffix_of_previous_token():
    zh = "他一定能活下。"
    order = build_order(zh, toks("他", "一定", "能活", "下", "。"), "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh


def test_whole_token_match():
    zh = "我告诉自己一定要冷静活下。"
    order = build_order(zh, toks("我", "告诉", "自己", "一定", "要", "冷静", "活下", "。"), "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh


def test_target_embedded_in_larger_token():
    zh = "他怎么可能知道。"
    order = build_order(zh, toks("他", "怎么可能", "知道", "。"), "怎么")
    assert targets(order) == ["怎么"]
    assert rejoined(order) == zh


def test_target_spanning_several_whole_tokens():
    zh = "他怎么可能知道。"
    order = build_order(zh, toks("他", "怎么", "可能", "知道", "。"), "怎么可能")
    assert targets(order) == ["怎么可能"]
    assert rejoined(order) == zh


def test_separable_word_both_parts():
    zh = "水由氢和氧组成。"
    order = build_order(zh, toks("水", "由", "氢", "和", "氧", "组成", "。"), "由...组成")
    assert targets(order) == ["由", "组成"]
    assert rejoined(order) == zh


def test_missing_target_appended_as_blank():
    """Word absent from the sentence: still ask for it, at the end."""
    zh = "今天天气很好。"
    order = build_order(zh, toks("今天", "天气", "很", "好", "。"), "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh + "活下"


def test_missing_separable_part_appended():
    zh = "水由氢和氧构成。"
    order = build_order(zh, toks("水", "由", "氢", "和", "氧", "构成", "。"), "由...组成")
    assert targets(order) == ["由", "组成"]
    assert rejoined(order) == zh + "组成"


def test_no_tokens_falls_back_to_characters():
    zh = "TÜV在严格监管中活下来。"
    order = build_order(zh, [], "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh


def test_malformed_tokens_fall_back_to_characters():
    """AI tokens that don't rejoin into the sentence must not shift offsets."""
    zh = "他一定能活下。"
    order = build_order(zh, toks("他", "一定"), "活下")
    assert targets(order) == ["活下"]
    assert rejoined(order) == zh


def test_french_space_separated_tokens_preserved():
    zh = "Il faut survivre ici."
    order = build_order(zh, toks("Il", " ", "faut", " ", "survivre", " ", "ici."), "survivre")
    assert targets(order) == ["survivre"]
    assert rejoined(order) == zh
    # Whitespace stays its own token so it can never become an empty tile
    assert {"type": "char", "char": " "} in order
