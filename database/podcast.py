"""Podcast crawler storage (issues #479, #497, #498, #502): episodes
discovered from podcast RSS feeds (podcast_feeds, one row per source, #502)
+ a small key-value config table (notification email, summary detail level,
enabled flag; the legacy `feeds` key is unused since #502 but kept for
backward compat, see database/core.py's one-time migration).

All SQL for the podcast feature lives here — podcast.py (the crawler logic)
and routes/podcast.py only call into this module.
"""
import json
from .core import get_db


# ---------------------------------------------------------------------------
# Config (key-value)
# ---------------------------------------------------------------------------

# Keys the crawler/UI is allowed to read or write. Kept in one place so
# routes/podcast.py's PUT endpoint can validate against the same whitelist.
# `whisper_fallback` (#485) is kept for backward compat, normalized into the
# newer `transcriber` key (#486) by podcast._resolve_transcriber.
# `notebooklm_notebook_id` is a crawler-internal cache, not meant to be set
# directly via the PUT endpoint. `channel_url`/`channel_id`/
# `whisper_title_filter` are retired (#497) — kept so old rows/installs don't
# break, no longer read by the crawler. `feeds` (#497, JSON array of RSS
# feed URLs) replaces `channel_url` as the source list.
CONFIG_KEYS = (
    "feeds", "email_to", "detail_level", "enabled", "channel_url", "channel_id",
    "whisper_fallback", "transcriber", "whisper_title_filter", "whisper_max_minutes",
    "notebooklm_notebook_id",
)


def get_podcast_config() -> dict:
    """All podcast_config rows as a flat {key: value} dict."""
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM podcast_config").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def set_podcast_config(key: str, value: str) -> None:
    """Upsert one config key. Used both by the crawler (caching channel_id)
    and the settings API (detail_level/enabled/email_to/channel_url)."""
    conn = get_db()
    conn.execute(
        "INSERT INTO podcast_config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Feeds (issue #502)
# ---------------------------------------------------------------------------

def list_feeds() -> list[dict]:
    """All configured RSS feeds, oldest-added first (created_at ASC) so the
    list order stays stable as new feeds are appended, with each feed's
    stored episode count attached."""
    conn = get_db()
    rows = conn.execute(
        """SELECT f.*, COUNT(e.id) AS episode_count
           FROM podcast_feeds f
           LEFT JOIN podcast_episodes e ON e.channel_id = f.url
           GROUP BY f.id
           ORDER BY f.created_at, f.id"""
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_feed(feed_id: int) -> dict | None:
    conn = get_db()
    row = conn.execute("SELECT * FROM podcast_feeds WHERE id = ?", (feed_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def create_feed(url: str, title: str | None = None, auto_process: int = 0) -> int:
    """Insert a new feed row. Raises sqlite3.IntegrityError (caller/route
    turns it into a 400) if `url` is already subscribed (UNIQUE constraint)."""
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO podcast_feeds (url, title, auto_process) VALUES (?, ?, ?)",
        (url, title, int(auto_process)),
    )
    conn.commit()
    feed_id = cur.lastrowid
    conn.close()
    return feed_id


def update_feed(feed_id: int, **fields) -> None:
    """Generic column update for a feed (title/auto_process)."""
    if not fields:
        return
    conn = get_db()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE podcast_feeds SET {set_clause} WHERE id = ?",
        (*fields.values(), feed_id),
    )
    conn.commit()
    conn.close()


def delete_feed(feed_id: int) -> None:
    """Remove a feed's subscription row. Episodes already ingested from it
    are left in place as history (channel_id keeps pointing at the feed URL,
    which no longer resolves to a podcast_feeds row)."""
    conn = get_db()
    conn.execute("DELETE FROM podcast_feeds WHERE id = ?", (feed_id,))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------

def get_episode_by_video_id(video_id: str) -> dict | None:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM podcast_episodes WHERE video_id = ?", (video_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_known_video_ids() -> set[str]:
    """Used to filter the RSS feed down to genuinely new videos."""
    conn = get_db()
    rows = conn.execute("SELECT video_id FROM podcast_episodes").fetchall()
    conn.close()
    return {r["video_id"] for r in rows}


def has_any_episode_for_feed(feed_url: str) -> bool:
    """True once at least one episode from this specific RSS feed (stored in
    the `channel_id` column, #497) has ever been stored — used to detect a
    feed's first crawl, which only backfills its latest FIRST_RUN_BACKFILL
    episodes instead of its entire back catalog."""
    conn = get_db()
    row = conn.execute(
        "SELECT 1 FROM podcast_episodes WHERE channel_id = ? LIMIT 1", (feed_url,)
    ).fetchone()
    conn.close()
    return row is not None


def create_pending_episode(video_id: str, channel_id: str | None, title: str,
                           published_at: str | None, youtube_url: str,
                           audio_url: str | None = None,
                           duration_seconds: int | None = None) -> int:
    """Insert a new episode row with status=pending. Returns the new id.

    `channel_id` stores the source RSS feed URL (#497, was a YouTube channel
    id pre-#497). `youtube_url` stores the episode's webpage link (item
    <link>; name kept for backward compat with existing rows/column).
    `audio_url`/`duration_seconds` (#497) come from the RSS enclosure and
    itunes:duration.
    """
    conn = get_db()
    cur = conn.execute(
        """INSERT INTO podcast_episodes
           (video_id, channel_id, title, published_at, youtube_url, audio_url, duration_seconds, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')""",
        (video_id, channel_id, title, published_at, youtube_url, audio_url, duration_seconds),
    )
    conn.commit()
    episode_id = cur.lastrowid
    conn.close()
    return episode_id


def update_episode(episode_id: int, **fields) -> None:
    """Generic column update for an episode. hsk_words (if present) is
    serialized to JSON automatically."""
    if not fields:
        return
    if "hsk_words" in fields and not isinstance(fields["hsk_words"], str):
        fields["hsk_words"] = json.dumps(fields["hsk_words"], ensure_ascii=False)
    conn = get_db()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE podcast_episodes SET {set_clause} WHERE id = ?",
        (*fields.values(), episode_id),
    )
    conn.commit()
    conn.close()


def _hydrate(row: dict) -> dict:
    d = dict(row)
    raw = d.get("hsk_words")
    try:
        d["hsk_words"] = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        d["hsk_words"] = []
    return d


def get_episode(episode_id: int) -> dict | None:
    """Full episode row (including transcript_zh) for the detail endpoint."""
    conn = get_db()
    row = conn.execute("SELECT * FROM podcast_episodes WHERE id = ?", (episode_id,)).fetchone()
    conn.close()
    return _hydrate(row) if row else None


def list_episodes(limit: int = 100, feed_url: str | None = None) -> list[dict]:
    """Episode list without the transcript full text (kept out for payload
    size). `feed_url` (#502, the podcast_feeds.url / episode's channel_id)
    optionally restricts the list to one source, for the per-feed episode
    list view."""
    conn = get_db()
    query = """SELECT id, video_id, channel_id, title, published_at, youtube_url, spotify_url,
                      audio_url, duration_seconds,
                      summary_de, hsk_words, detail_level, status, error, email_sent_at, created_at,
                      transcript_source,
                      (transcript_zh IS NOT NULL AND transcript_zh != '') AS has_transcript
               FROM podcast_episodes"""
    params: list = []
    if feed_url:
        query += " WHERE channel_id = ?"
        params.append(feed_url)
    query += " ORDER BY COALESCE(published_at, created_at) DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [_hydrate(r) for r in rows]


def list_recent_error_episodes(max_age_days: int = 7) -> list[dict]:
    """Episodes with status='error' created within the last `max_age_days`
    days — run_check's automatic retry window (#491). Older failures are left
    alone so a permanently-broken video can't be retried (and billed) forever.
    created_at is stored via datetime('now') (UTC), so the comparison uses
    the same clock."""
    conn = get_db()
    rows = conn.execute(
        """SELECT id, video_id, title, audio_url, duration_seconds FROM podcast_episodes
           WHERE status = 'error' AND created_at >= datetime('now', ?)
           ORDER BY id""",
        (f"-{int(max_age_days)} days",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def word_zh_exists(words: list[str]) -> set[str]:
    """Given candidate HSK words, return the subset already present in
    entries.word_zh — used to filter the AI's word list down to genuinely
    new vocabulary before it's shown to Daniel."""
    if not words:
        return set()
    conn = get_db()
    placeholders = ",".join("?" for _ in words)
    rows = conn.execute(
        f"SELECT word_zh FROM entries WHERE word_zh IN ({placeholders})", words
    ).fetchall()
    conn.close()
    return {r["word_zh"] for r in rows}
