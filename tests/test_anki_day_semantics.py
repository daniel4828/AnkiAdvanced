"""Guard for #810: tests must express "today" the way the app does.

Anki days start at the preset's cutoff hour (4 a.m. by default), not at
midnight. Production code derives every deck name and due date from
database.anki_today(); a test that builds its expectation from
datetime.date.today() therefore disagrees with the app for four hours every
night, and five tests really did fail on any local run started before 4 a.m.

Two guards here: the real behavioral one (anki_today respects the cutoff),
and a cheap grep-style one so the pattern doesn't creep back in — the same
approach tests/test_add_word.py uses to keep addWordViaAi from being copied
into app.js a second time.
"""
import os
import re
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import database

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def test_anki_today_respects_the_cutoff_hour(monkeypatch):
    """Before the cutoff, the Anki day is still yesterday's."""
    monkeypatch.setattr(database.core, "_DAY_CUTOFF_HOUR", 4, raising=False)
    monkeypatch.setattr(database.core, "get_day_cutoff_hour", lambda: 4)

    real = datetime(2026, 8, 19, 1, 30)  # 01:30 — before the cutoff

    class _FakeDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return real

    monkeypatch.setattr(database.core, "datetime", _FakeDateTime)

    assert database.core.anki_today() == date(2026, 8, 18)
    assert database.core.anki_today() != real.date()

    after = real.replace(hour=9)
    monkeypatch.setattr(_FakeDateTime, "now", classmethod(lambda cls, tz=None: after))
    assert database.core.anki_today() == date(2026, 8, 19)


def test_no_test_builds_a_day_expectation_from_date_today():
    """date.today() in a test is almost always the #810 bug.

    If a future test genuinely needs the wall-clock date (something unrelated
    to Anki days), give it a different spelling — datetime.now().date() — and
    say why in a comment, rather than weakening this guard.
    """
    offenders = []
    for name in sorted(os.listdir(_TESTS_DIR)):
        if not name.startswith("test_") or not name.endswith(".py"):
            continue
        if name == os.path.basename(__file__):
            continue
        text = open(os.path.join(_TESTS_DIR, name), encoding="utf-8").read()
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue          # the explanatory comments name it on purpose
            if re.search(r"\bdate\.today\(\)", line):
                offenders.append(f"{name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "use database.anki_today() instead of date.today() (#810):\n"
        + "\n".join(offenders)
    )
