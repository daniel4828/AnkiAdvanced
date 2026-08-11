"""复习收尾提醒的测试（议题 #701）。

这个功能的价值全在「什么时候**不**发」：分批到期时发信等于把 Daniel 叫回来
做两张卡再干等十分钟，那还不如不提醒。所以下面每条否定用例都和肯定用例同等
重要。
"""

from datetime import datetime, timedelta

import pytest

pytest.importorskip("fastapi", reason="fastapi not installed")

import database
import database.core
import review_notify


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    # database.core.DB_PATH，不是 database.DB_PATH —— 见 conftest.py（#615）。
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return tmp_path / "test.db"


@pytest.fixture
def mail(monkeypatch):
    """拦住 SMTP，记录每次发信。"""
    sent = []

    def _fake_send_mail(subject, body_html, *, context="mail"):
        sent.append({"subject": subject, "body": body_html, "context": context})
        return True

    monkeypatch.setattr(review_notify.podcast, "send_mail", _fake_send_mail)
    return sent


def _deck() -> int:
    return database.get_or_create_deck("NotifyDeck")


def _card(deck_id: int, word: str, state: str, due: str) -> int:
    """建一张指定状态与到期时刻的卡片。"""
    word_id = database.insert_word({"word_zh": word, "definition": word})
    card_id = database.insert_card(word_id, "listening", deck_id, state=state, due=due)
    conn = database.get_db()
    conn.execute("UPDATE cards SET state = ?, due = ? WHERE id = ?", (state, due, card_id))
    conn.commit()
    conn.close()
    return card_id


def _now_minus(minutes: int) -> str:
    return (datetime.now() - timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _now_plus(minutes: int) -> str:
    return (datetime.now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 判定逻辑
# ---------------------------------------------------------------------------

def test_ready_when_all_leftovers_are_due(tmp_db):
    """队列空过、Again 的卡全部重新到期 → 该发。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    _card(deck, "词二", "relearn", _now_minus(1))

    status = database.due_notification_status()
    assert status["due_now"] == 2
    assert status["later_today"] == 0
    assert status["other_due"] == 0
    assert status["ready"] is True


def test_not_ready_while_another_card_is_still_waiting(tmp_db):
    """还有一张卡十分钟后才回来 → 不发。这是本功能的核心条件。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    _card(deck, "词二", "learning", _now_plus(10))

    status = database.due_notification_status()
    assert status["due_now"] == 1
    assert status["later_today"] == 1
    assert status["ready"] is False


def test_cards_on_the_1d_step_do_not_block(tmp_db):
    """1d/3d 学习步骤的卡到期在明天日界之后，属于别的一天，不该拦住今天的提醒
    —— 否则等它们就永远发不出去。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    _card(deck, "词二", "learning",
          (datetime.now() + timedelta(days=1)).isoformat(timespec="seconds"))

    status = database.due_notification_status()
    assert status["later_today"] == 0
    assert status["ready"] is True


def test_not_ready_when_the_queue_never_emptied(tmp_db):
    """还有普通到期卡没复习 → 队列根本没空过，没有"收尾"可言。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    _card(deck, "词二", "review", database.anki_today().isoformat())

    status = database.due_notification_status()
    assert status["other_due"] == 1
    assert status["ready"] is False


def test_not_ready_with_nothing_due(tmp_db):
    """一张到期的收尾卡都没有 → 没什么可提醒的。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_plus(10))

    status = database.due_notification_status()
    assert status["due_now"] == 0
    assert status["ready"] is False


def test_suspended_and_deleted_cards_are_ignored(tmp_db):
    """挂起/删除的卡既不触发提醒，也不拦住提醒。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    _card(deck, "词二", "suspended", _now_plus(10))
    deleted = _card(deck, "词三", "learning", _now_plus(10))
    conn = database.get_db()
    conn.execute("UPDATE cards SET deleted_at = datetime('now') WHERE id = ?", (deleted,))
    conn.commit()
    conn.close()

    status = database.due_notification_status()
    assert status["later_today"] == 0
    assert status["ready"] is True


# ---------------------------------------------------------------------------
# 发信与去重
# ---------------------------------------------------------------------------

def test_sends_once_per_anki_day(tmp_db, mail):
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))

    first = review_notify.check_and_notify()
    assert first["sent"] is True
    assert len(mail) == 1
    assert "1" in mail[0]["subject"]

    second = review_notify.check_and_notify()
    assert second["sent"] is False
    assert second["already_sent_today"] is True
    assert len(mail) == 1


def test_force_bypasses_the_daily_dedup(tmp_db, mail):
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))

    review_notify.check_and_notify()
    review_notify.check_and_notify(force=True)
    assert len(mail) == 2


def test_force_still_respects_the_condition(tmp_db, mail):
    """force 只跳过去重，条件不成立照样不发。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_plus(10))

    result = review_notify.check_and_notify(force=True)
    assert result["sent"] is False
    assert mail == []


def test_no_mark_written_when_smtp_is_unconfigured(tmp_db, monkeypatch):
    """SMTP 没配置是"跳过"不是"已发送"：不能写标记，否则配好之后当天再也收不到。"""
    deck = _deck()
    _card(deck, "词一", "learning", _now_minus(5))
    monkeypatch.setattr(review_notify.podcast, "send_mail", lambda *a, **k: False)

    result = review_notify.check_and_notify()
    assert result["sent"] is False
    assert database.get_app_setting(review_notify.LAST_SENT_KEY) is None
