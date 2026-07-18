"""get_api_costs 的动作分组测试（issue #578）。

重点：相邻的同标签 "… Again Sentences" 动作在显示时合并成一行；
非 Again 标签、间隔过大或被其他动作隔开的不合并。
"""
import uuid

import pytest

import database
import database.core


@pytest.fixture()
def db(tmp_path, monkeypatch):
    # 与 test_queue_manager 相同的 DB_PATH monkeypatch 模式。
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


def _insert(called_at: str, label: str | None, purpose: str = "story") -> None:
    """直接插入一条 1 调用的动作行（每个动作独立 action_id，模拟一次 Again 点击）。"""
    conn = database.core.get_db()
    conn.execute(
        """INSERT INTO api_call_log
           (called_at, model, input_tokens, output_tokens, purpose,
            action_id, action_label)
           VALUES (?, 'deepseek-chat', 100, 50, ?, ?, ?)""",
        (called_at, purpose, uuid.uuid4().hex if label else None, label),
    )
    conn.commit()
    conn.close()


def _labels(limit: int = 100) -> list[tuple[str, int]]:
    actions = database.get_api_costs(limit)["actions"]
    return [(a["label"], a["call_count"]) for a in actions]


def test_adjacent_again_actions_merge(db):
    _insert("2026-07-17 18:50:00", "Podcast Again Sentences")
    _insert("2026-07-17 18:53:00", "Podcast Again Sentences")
    _insert("2026-07-17 19:00:00", "Podcast Again Sentences")
    assert _labels() == [("Podcast Again Sentences", 3)]


def test_different_again_labels_do_not_merge(db):
    _insert("2026-07-17 18:34:00", "Story Again Sentences")
    _insert("2026-07-17 18:50:00", "Podcast Again Sentences")
    assert _labels() == [
        ("Podcast Again Sentences", 1),
        ("Story Again Sentences", 1),
    ]


def test_interleaved_other_action_breaks_merge(db):
    _insert("2026-07-17 18:50:00", "Podcast Again Sentences")
    _insert("2026-07-17 18:55:00", "Story Again Sentences")
    _insert("2026-07-17 19:00:00", "Podcast Again Sentences")
    assert _labels() == [
        ("Podcast Again Sentences", 1),
        ("Story Again Sentences", 1),
        ("Podcast Again Sentences", 1),
    ]


def test_large_gap_does_not_merge(db):
    _insert("2026-07-17 10:00:00", "Podcast Again Sentences")
    _insert("2026-07-17 12:00:00", "Podcast Again Sentences")  # 2h > 30min gap
    assert _labels() == [
        ("Podcast Again Sentences", 1),
        ("Podcast Again Sentences", 1),
    ]


def test_non_again_labels_never_merge(db):
    _insert("2026-07-17 18:50:00", "Generate Story: All")
    _insert("2026-07-17 18:51:00", "Generate Story: All")
    assert _labels() == [
        ("Generate Story: All", 1),
        ("Generate Story: All", 1),
    ]


def test_legacy_null_rows_keep_time_clustering(db):
    # NULL action_id 行仍走 #537 时间聚类：60 秒内归一簇，标签 "<purpose> · legacy"
    _insert("2026-07-17 18:50:00", None, purpose="podcast")
    _insert("2026-07-17 18:50:30", None, purpose="podcast")
    assert _labels() == [("podcast · legacy", 2)]
