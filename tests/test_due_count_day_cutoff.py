"""牌组角标与复习队列在日界点前后必须说同一套话（议题 #762）。

learning/relearn 卡的 `due` 有两种形式：分钟级步骤（1m/10m）存 ISO datetime，
跨日步骤（1d/3d）存**纯日期**。纯日期表示「那个 Anki 日一开始就到期」。

凌晨 0 点到日界点（默认 4–5 点）之间 Anki 日还是昨天，此时一张 due 为**今天
日历日期**的卡属于**下一个** Anki 日，现在不该出现。原来计数用
`due <= now` 做字符串比较，`'2026-08-16' <= '2026-08-16T03:13:53'` 为真，于是
角标显示几十张卡，而队列（`due < tomorrow`）一张都不给 —— 点进牌组直接
「全部完成」。

下面的用例把这个时刻钉死：计数与 `get_due_cards()` 必须返回同一批卡。
"""

from datetime import datetime

import pytest

import database
import database.cards
import database.core

# 凌晨 3:13 —— 日界点之前，所以 Anki 日仍是 8-15。
_FIXED_NOW = datetime(2026, 8, 16, 3, 13, 0)
_TOMORROW_DATE = "2026-08-16"   # 纯日期 due：属于下一个 Anki 日
_TODAY_DATE = "2026-08-15"      # 纯日期 due：当前 Anki 日，已到期


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _FIXED_NOW


@pytest.fixture
def before_cutoff(tmp_path, monkeypatch):
    """临时库 + 冻结在凌晨 3:13（日界点 4 点之前）。"""
    # database.core.DB_PATH，不是 database.DB_PATH —— 见 conftest.py（#615）。
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    monkeypatch.setattr(database.core, "_day_cutoff_hour", 4)
    monkeypatch.setattr(database.core, "datetime", _FakeDatetime)
    monkeypatch.setattr(database.cards, "datetime", _FakeDatetime)
    assert database.anki_today().isoformat() == _TODAY_DATE


def _card(deck_id: int, word: str, due: str, state: str = "learning") -> int:
    word_id = database.insert_word({"word_zh": word, "definition": word})
    card_id = database.insert_card(word_id, "listening", deck_id, state=state, due=due)
    conn = database.get_db()
    conn.execute("UPDATE cards SET state = ?, due = ? WHERE id = ?", (state, due, card_id))
    conn.commit()
    conn.close()
    return card_id


def test_counts_and_queue_agree_before_day_cutoff(before_cutoff):
    deck_id = database.get_or_create_deck("CutoffDeck")
    tomorrow_step = _card(deck_id, "明天", _TOMORROW_DATE)          # 1d/3d 步骤，属于下一天
    passed_dt = _card(deck_id, "刚才", "2026-08-16T03:00:00")       # 分钟级步骤，已过
    yesterday_step = _card(deck_id, "昨天", _TODAY_DATE)            # 当前 Anki 日的跨日步骤

    counts = database.count_due(deck_id, "listening")
    assert counts["learning"] == 2, "纯日期 due 落在下一个 Anki 日的卡不该算作已到期"
    assert counts["learning_future"] == 1, "它必须落进 learning_future，不能两边都不算"

    queued = {c["id"] for c in database.get_due_cards(deck_id, "listening")}
    assert queued == {passed_dt, yesterday_step}
    assert tomorrow_step not in queued

    # 角标（count_due_all_decks）与单牌组计数、与队列三者必须一致。
    all_counts, _ = database.count_due_all_decks()
    bulk = all_counts[(deck_id, "listening")]
    assert bulk["learning"] == len(queued)
    assert bulk["learning_future"] == 1


def test_after_cutoff_the_same_card_is_due(before_cutoff, monkeypatch):
    """过了日界点，同一张纯日期卡必须出现 —— 修复不能把它永久藏起来。"""
    deck_id = database.get_or_create_deck("CutoffDeck")
    card_id = _card(deck_id, "明天", _TOMORROW_DATE)

    class _AfterCutoff(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 16, 9, 0, 0)

    monkeypatch.setattr(database.core, "datetime", _AfterCutoff)
    monkeypatch.setattr(database.cards, "datetime", _AfterCutoff)
    assert database.anki_today().isoformat() == _TOMORROW_DATE

    assert database.count_due(deck_id, "listening")["learning"] == 1
    assert {c["id"] for c in database.get_due_cards(deck_id, "listening")} == {card_id}
