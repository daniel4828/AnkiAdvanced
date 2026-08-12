"""Tests for the version badge's deploy timestamp (issue #706).

The badge showed the production server's Asia/Shanghai clock digits no matter
where it was read, because `deployed_at` carried no UTC offset and the browser
parses an offset-less ISO date-time as *its own* local time. The offset is the
root fix — without it no client-side formatting can recover the real instant.
"""
import pathlib
import re
from datetime import datetime

from fastapi.testclient import TestClient

import main


def test_deployed_at_carries_a_utc_offset():
    resp = TestClient(main.app).get("/api/version")
    assert resp.status_code == 200
    deployed_at = resp.json()["deployed_at"]
    # Parsing an offset-less string yields a naive datetime — exactly the bug.
    assert datetime.fromisoformat(deployed_at).tzinfo is not None, deployed_at


def test_badge_renders_in_berlin_regardless_of_browser_timezone():
    """The server travels (Asia/Shanghai) and so does the phone, so neither of
    those is the timezone the badge should be read in — pin it like podcast
    episode dates (#532)."""
    app_js = pathlib.Path("static/app.js").read_text(encoding="utf-8")
    fn = re.search(r"function _formatBerlin\(iso\) \{.*?\n\}", app_js, re.S)
    assert fn, "_formatBerlin() is gone — the badge would fall back to browser-local time"
    assert "timeZone: 'Europe/Berlin'" in fn.group(0)
    # getHours()/getDate() read the browser's timezone back out, which is what
    # made the badge wrong in the first place.
    badge = re.search(r"async function _loadVersionBadge\(\) \{.*?\n\}", app_js, re.S).group(0)
    assert "getHours()" not in badge and "getDate()" not in badge
