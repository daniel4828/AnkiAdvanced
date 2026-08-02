"""离线模式同步测试（issue #612）。

核心保证：`push` 只把 cards + review_log 合并回生产库，离线期间服务器上
新增的数据（播客单集、预生成故事、成本日志）一行都不能丢；同一份离线库
不能被合并第二次。
"""
import importlib.util
import os
import sqlite3

import pytest

os.environ.setdefault("DISABLE_AI", "1")

import database
import database.core

_SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "offline_sync_server.py")
_spec = importlib.util.spec_from_file_location("offline_sync_server", _SCRIPT)
sync_server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync_server)


def _make_db(path, monkeypatch):
    monkeypatch.setattr(database.core, "DB_PATH", str(path))
    database.init_db()


@pytest.fixture()
def server_db(tmp_path, monkeypatch):
    """A production-like database with one leaf deck, one entry and one card."""
    path = tmp_path / "srs.db"
    _make_db(path, monkeypatch)
    conn = sqlite3.connect(path)
    # deck 1 ("All") and preset 1 are seeded by init_db()
    conn.execute("INSERT INTO decks (id, name, category, preset_id, parent_id) "
                 "VALUES (2, 'Test', 'reading', 1, 1)")
    conn.execute("INSERT INTO entries (id, word_zh, definition) VALUES (1, '测试', 'test')")
    conn.execute("INSERT INTO cards (id, word_id, deck_id, category, state, due) "
                 "VALUES (1, 1, 2, 'reading', 'new', '2026-08-01')")
    conn.commit()
    conn.close()
    return str(path)


def _prepare(server_db, tmp_path):
    snapshot = str(tmp_path / "offline.db")
    sync_server.cmd_prepare(server_db, snapshot)
    return snapshot


def _review_offline(offline_db, card_id=1, rating=3, due='2026-08-05'):
    """Simulate one review done on the plane: log it and advance the card."""
    conn = sqlite3.connect(offline_db)
    conn.execute("INSERT INTO review_log (card_id, reviewed_at, rating) VALUES (?, ?, ?)",
                 (card_id, '2026-08-03T10:00:00', rating))
    conn.execute("UPDATE cards SET state = 'review', due = ?, interval = 4 WHERE id = ?",
                 (due, card_id))
    conn.commit()
    conn.close()


def test_prepare_snapshot_carries_token(server_db, tmp_path):
    snapshot = _prepare(server_db, tmp_path)
    assert os.path.exists(snapshot)
    server_token = sqlite3.connect(server_db).execute(
        "SELECT value FROM app_settings WHERE key = 'offline_sync_token'").fetchone()[0]
    offline_token = sqlite3.connect(snapshot).execute(
        "SELECT value FROM app_settings WHERE key = 'offline_sync_token'").fetchone()[0]
    assert server_token == offline_token


def test_merge_applies_reviews_and_card_state(server_db, tmp_path):
    snapshot = _prepare(server_db, tmp_path)
    _review_offline(snapshot)

    sync_server.cmd_merge(server_db, snapshot)

    conn = sqlite3.connect(server_db)
    logs = conn.execute("SELECT card_id, rating FROM review_log").fetchall()
    assert logs == [(1, 3)]
    state, due, interval = conn.execute(
        "SELECT state, due, interval FROM cards WHERE id = 1").fetchone()
    assert (state, due, interval) == ('review', '2026-08-05', 4)


def test_merge_keeps_data_the_server_created_while_offline(server_db, tmp_path):
    """The cron jobs keep running while Daniel is on the plane — a story added
    after the snapshot must survive the merge."""
    snapshot = _prepare(server_db, tmp_path)
    _review_offline(snapshot)

    conn = sqlite3.connect(server_db)
    conn.execute("INSERT INTO stories (deck_id, date, category, topic) "
                 "VALUES (2, '2026-08-03', 'reading', '服务器上新生成的故事')")
    conn.commit()
    conn.close()

    sync_server.cmd_merge(server_db, snapshot)

    conn = sqlite3.connect(server_db)
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 1


def test_second_merge_of_same_file_is_refused(server_db, tmp_path):
    snapshot = _prepare(server_db, tmp_path)
    _review_offline(snapshot)
    sync_server.cmd_merge(server_db, snapshot)

    with pytest.raises(SystemExit) as exc:
        sync_server.cmd_merge(server_db, snapshot)
    assert "token mismatch" in str(exc.value)
    # the first merge's rows are still there, not duplicated
    assert sqlite3.connect(server_db).execute(
        "SELECT COUNT(*) FROM review_log").fetchone()[0] == 1


def test_merge_skips_reviews_for_cards_deleted_on_the_server(server_db, tmp_path):
    snapshot = _prepare(server_db, tmp_path)
    _review_offline(snapshot)

    conn = sqlite3.connect(server_db)
    conn.execute("DELETE FROM cards WHERE id = 1")
    conn.commit()
    conn.close()

    sync_server.cmd_merge(server_db, snapshot)  # must not raise a foreign-key error
    assert sqlite3.connect(server_db).execute(
        "SELECT COUNT(*) FROM review_log").fetchone()[0] == 0


def test_merge_without_prepare_is_refused(server_db, tmp_path):
    """A hand-copied database has no token — refuse rather than guess."""
    stray = str(tmp_path / "stray.db")
    _make_db(tmp_path / "stray.db", pytest.MonkeyPatch())
    with pytest.raises(SystemExit) as exc:
        sync_server.cmd_merge(server_db, stray)
    assert "never prepared" in str(exc.value)


def test_slim_strips_bulk_from_the_snapshot_only(server_db, tmp_path):
    """The snapshot loses the megabyte-heavy columns; production keeps them."""
    conn = sqlite3.connect(server_db)
    conn.execute("INSERT INTO api_call_log (model, input_tokens, output_tokens, prompt) "
                 "VALUES ('m', 1, 1, '很长的提示词')")
    conn.execute("INSERT INTO podcast_episodes (video_id, title, youtube_url, "
                 "transcript_zh, summary_de) VALUES ('v', 't', 'u', '长转录', '摘要')")
    conn.execute("INSERT INTO stories (deck_id, date, category, prompt_text) "
                 "VALUES (2, '2026-08-02', 'reading', '很长的提示词')")
    conn.commit()
    conn.close()

    snapshot = _prepare(server_db, tmp_path)

    snap = sqlite3.connect(snapshot)
    assert snap.execute("SELECT COUNT(*) FROM api_call_log").fetchone()[0] == 0
    assert snap.execute("SELECT transcript_zh FROM podcast_episodes").fetchone()[0] is None
    assert snap.execute("SELECT prompt_text FROM stories").fetchone()[0] is None
    # the small, still-useful podcast fields survive
    assert snap.execute("SELECT summary_de FROM podcast_episodes").fetchone()[0] == '摘要'

    prod = sqlite3.connect(server_db)
    assert prod.execute("SELECT COUNT(*) FROM api_call_log").fetchone()[0] == 1
    assert prod.execute("SELECT transcript_zh FROM podcast_episodes").fetchone()[0] == '长转录'
    assert prod.execute("SELECT prompt_text FROM stories").fetchone()[0] == '很长的提示词'


def test_slimmed_snapshot_still_merges(server_db, tmp_path):
    snapshot = _prepare(server_db, tmp_path)   # slimming runs by default
    _review_offline(snapshot)
    sync_server.cmd_merge(server_db, snapshot)
    assert sqlite3.connect(server_db).execute(
        "SELECT due FROM cards WHERE id = 1").fetchone()[0] == '2026-08-05'


def test_untouched_cards_are_not_rewritten(server_db, tmp_path):
    """Only rows that actually differ get an UPDATE — a no-op sync changes nothing."""
    snapshot = _prepare(server_db, tmp_path)
    before = sqlite3.connect(server_db).execute(
        "SELECT state, due FROM cards WHERE id = 1").fetchone()

    sync_server.cmd_merge(server_db, snapshot)

    after = sqlite3.connect(server_db).execute(
        "SELECT state, due FROM cards WHERE id = 1").fetchone()
    assert before == after
