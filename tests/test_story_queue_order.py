"""复习队列的故事排序（议题 #732）。

场景：早上复习了一半 → 加新词 → 重新生成故事。新故事只覆盖生成那一刻到期的
词，所以早上剩下的卡（尤其按了 Again 的）可能不在里面。原来它们被赋予一个最大
排序值，落到**所有新卡之后**，新卡多的日子里永远轮不到——牌组角标却照常显示，
看起来就像卡片丢了。

这里守住的契约：有故事时，学习/复习态的欠账仍排在新卡之前。
"""
import datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database
import database.core


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """必须打 database.core.DB_PATH（见 conftest.py 与议题 #615）。"""
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test_srs.db"))
    database.init_db()


# ---------------------------------------------------------------------------
# story_sort_key —— 纯函数，不碰数据库
# ---------------------------------------------------------------------------

def test_key_groups_leftovers_before_story_before_new():
    pos = {10: 0, 11: 1}
    in_story = {"word_id": 10, "state": "new"}
    leftover = {"word_id": 99, "state": "relearn"}
    new_outside = {"word_id": 98, "state": "new"}

    assert database.story_sort_key(leftover, pos) < database.story_sort_key(in_story, pos)
    assert database.story_sort_key(in_story, pos) < database.story_sort_key(new_outside, pos)


def test_key_keeps_narrative_order_inside_the_story():
    pos = {10: 0, 11: 1}
    first = database.story_sort_key({"word_id": 10, "state": "review"}, pos)
    second = database.story_sort_key({"word_id": 11, "state": "review"}, pos)
    assert first < second


def test_key_without_story_treats_everything_as_outside():
    """空故事映射时仍然是学习态优先——和 get_due_cards_any_cat 的无故事分支一致。"""
    learning = database.story_sort_key({"word_id": 1, "state": "learning"}, {})
    new = database.story_sort_key({"word_id": 2, "state": "new"}, {})
    assert learning < new


# ---------------------------------------------------------------------------
# get_due_cards_any_cat —— 端到端复现议题 #732
# ---------------------------------------------------------------------------

def _add_entry(word: str) -> int:
    conn = database.core.get_db()
    cur = conn.execute(
        "INSERT INTO entries (word_zh, pinyin, definition, note_type, lang) "
        "VALUES (?, 'x', ?, 'vocabulary', 'zh')",
        (word, word),
    )
    conn.commit()
    wid = cur.lastrowid
    conn.close()
    return wid


def test_leftover_due_cards_are_not_pushed_behind_new_cards(tmp_db):
    root = database.get_or_create_deck("All")
    daily = database.get_or_create_deck("Daily · test", parent_id=root)
    leaves = database.get_or_create_category_decks(daily, "Daily · test")

    today = database.anki_today().isoformat()
    now = datetime.datetime.now().isoformat(timespec="seconds")

    leftover_ids = []
    for word in ("剩余一", "剩余二"):
        wid = _add_entry(word)
        leftover_ids.append(
            database.insert_card(wid, "listening", leaves["listening"],
                                 state="relearn", due=now))
    new_ids = []
    for word in ("新词一", "新词二", "新词三"):
        wid = _add_entry(word)
        new_ids.append(
            database.insert_card(wid, "listening", leaves["listening"],
                                 state="new", due=today))

    # 重新生成的故事只覆盖新词——剩余的卡在生成那一刻已被复习过，不在到期集合里
    story_sentences = [
        {"position": i, "sentence_zh": f"句子{i}",
         "word_ids": [database.get_card(cid)["word_id"]]}
        for i, cid in enumerate(new_ids)
    ]
    database.create_story(today, "unified", root, story_sentences,
                          gen_params={"mode": "knowledge"}, lang="zh")

    order = [c["id"] for c in database.get_due_cards_any_cat(root)]

    assert set(order) == set(leftover_ids + new_ids), "到期卡一张都不能丢"
    last_leftover = max(order.index(cid) for cid in leftover_ids)
    first_new = min(order.index(cid) for cid in new_ids)
    assert last_leftover < first_new, (
        "有故事时，不在故事里的到期卡被排到了新卡之后（议题 #732 回归）"
    )


# ---------------------------------------------------------------------------
# 生成故事后必须失效会话队列（议题 #783）
# ---------------------------------------------------------------------------

def _run_generate(monkeypatch, body_result):
    """跑 _generate_and_store，桩掉真正的生成逻辑，返回 invalidate 的调用次数。"""
    from routes import story as story_routes

    calls = []
    monkeypatch.setattr(story_routes.queue_mgr, "invalidate",
                        lambda *a, **kw: calls.append(1))
    monkeypatch.setattr(story_routes.ai, "fix_definition_commas", lambda *a, **kw: None)
    monkeypatch.setattr(story_routes, "_generate_and_store_body",
                        lambda *a, **kw: body_result)
    story_routes._generate_and_store(
        1, "unified", "2026-08-16", [{"word_zh": "词"}],
        topic=None, max_hsk=3, model="x", grammar_focus=None, grammar_pct=75,
        mode="story", chapter_ids=None, progress_key="1/unified/zh", lang="zh")
    return len(calls)


def test_generation_invalidates_queue(tmp_db, monkeypatch):
    """故事由 GET /api/story 首次生成时队列也必须失效（#783）：
    队列是持久的，构建时若还没有故事就按 learning-first 排序，不失效的话
    整个复习会话都从故事中间某一句开始。"""
    assert _run_generate(monkeypatch, {"id": 1, "sentences": []}) == 1


def test_failed_generation_keeps_queue(tmp_db, monkeypatch):
    """生成失败时故事没变，不该白白丢掉队列。"""
    assert _run_generate(monkeypatch, {"error": True, "reason": "boom"}) == 0
    assert _run_generate(monkeypatch, None) == 0
