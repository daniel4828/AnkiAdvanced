"""Regression tests for the #497 legacy-YouTube-row purge in database.core
(issue #688).

That purge deletes podcast_episodes rows whose video_id looks like a YouTube
id (11 chars) and which never reached 'summarized'. The knowledge base (#651)
stores exactly that video_id shape for every video it ingests, and init_db()
runs on every startup — so freshly added videos were deleted mid-processing,
every couple of minutes, on the production server. These tests pin down both
guards: kind-scoping and the one-shot marker.
"""

import importlib

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh initialized database at a throwaway path."""
    import database.core

    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "purge.db"))
    database.core.init_db()
    return database.core


def _insert(db, *, video_id, kind, status):
    conn = db.get_db()
    conn.execute(
        """INSERT INTO podcast_episodes (video_id, title, youtube_url, status, kind)
           VALUES (?, ?, ?, ?, ?)""",
        (video_id, "T", f"https://example.com/{video_id}", status, kind),
    )
    conn.commit()
    conn.close()


def _video_ids(db):
    conn = db.get_db()
    ids = {r["video_id"] for r in conn.execute("SELECT video_id FROM podcast_episodes")}
    conn.close()
    return ids


def test_pending_video_survives_restart(db):
    """The actual #688 bug: a knowledge-base video still being transcribed
    must not be deleted by the next init_db()."""
    _insert(db, video_id="dQw4w9WgXcQ", kind="video", status="pending")
    db.init_db()
    assert "dQw4w9WgXcQ" in _video_ids(db)


def test_article_and_summarized_rows_survive_restart(db):
    _insert(db, video_id="abcdefghijk", kind="article", status="error")
    _insert(db, video_id="lmnopqrstuv", kind="video", status="summarized")
    db.init_db()
    assert {"abcdefghijk", "lmnopqrstuv"} <= _video_ids(db)


def test_purge_runs_only_once(db):
    """The legacy cleanup already ran during the fixture's init_db(), so a
    podcast row inserted afterwards is never purged — a destructive cleanup
    must not keep firing on every startup."""
    _insert(db, video_id="ABCDEFGHIJK", kind="podcast", status="error")
    db.init_db()
    assert "ABCDEFGHIJK" in _video_ids(db)


def test_legacy_podcast_row_is_purged_on_first_run(tmp_path, monkeypatch):
    """The original #497 behavior still works on a database that has not been
    purged yet: a stuck legacy YouTube row (kind='podcast') is removed."""
    import database.core as core

    monkeypatch.setattr(core, "DB_PATH", str(tmp_path / "legacy.db"))
    core.init_db()
    conn = core.get_db()
    conn.execute("DELETE FROM app_settings WHERE key = 'purged_legacy_youtube_rows'")
    conn.execute(
        """INSERT INTO podcast_episodes (video_id, title, youtube_url, status, kind)
           VALUES ('ZYXWVUTSRQP', 'legacy', 'https://y/ZYXWVUTSRQP', 'error', 'podcast')""")
    conn.commit()
    conn.close()

    core.init_db()

    conn = core.get_db()
    remaining = {r["video_id"] for r in conn.execute("SELECT video_id FROM podcast_episodes")}
    conn.close()
    assert "ZYXWVUTSRQP" not in remaining
