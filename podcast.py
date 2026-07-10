"""Podcast crawler (issue #479): discover new videos on a YouTube channel,
download the Chinese transcript, summarize into German + HSK5+ vocabulary via
AI, find a Spotify link, and email a notification.

Style follows news_fetcher.py: mostly-pure functions + logger, one module for
the whole pipeline. The one heavier dependency is yt-dlp (requirements.txt),
used purely for metadata + subtitle URL extraction (skip_download=True — no
video/audio is ever downloaded).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from xml.etree import ElementTree

import ai
import database

logger = logging.getLogger(__name__)

# On the very first run (no episodes in the DB yet) only backfill this many
# of the most recent videos — otherwise the first crawl of an established
# channel would try to transcribe/summarize its entire back catalog.
FIRST_RUN_BACKFILL = 5

# Manual/automatic Chinese caption language codes to try, in priority order.
# Manual captions (human-made) are always preferred over automatic ones.
_ZH_LANG_CANDIDATES = ("zh-Hans", "zh-CN", "zh", "zh-Hant", "zh-TW", "zh-HK")

# YouTube's channel page embeds the id under different keys depending on
# where in the page JSON it appears — "channelId" is the classic one but
# current pages more reliably expose "externalId"/"browseId". Try them in
# order and take the first match.
_CHANNEL_ID_RE = re.compile(r'"(?:channelId|externalId|browseId)":"(UC[\w-]+)"')


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ---------------------------------------------------------------------------
# Channel discovery
# ---------------------------------------------------------------------------

def resolve_channel_id(channel_url: str) -> str:
    """Resolve a YouTube channel handle URL (e.g. .../@shengfm/videos) to its
    stable UC... channel id, by scraping the channel page HTML. Cached in
    podcast_config so this only runs once per channel.
    """
    cfg = database.get_podcast_config()
    cached = cfg.get("channel_id")
    if cached:
        return cached

    html = _http_get(channel_url).decode("utf-8", errors="replace")
    m = _CHANNEL_ID_RE.search(html)
    if not m:
        raise ValueError(f"Could not find channelId in {channel_url}")
    channel_id = m.group(1)
    database.set_podcast_config("channel_id", channel_id)
    logger.info("podcast: resolved channel_id=%s for %s", channel_id, channel_url)
    return channel_id


def fetch_new_videos() -> list[dict]:
    """Return videos from the channel RSS feed that aren't in the DB yet
    (newest first, per YouTube's feed order). Zero API quota — RSS is public.

    On the first-ever run (empty podcast_episodes table), only the latest
    FIRST_RUN_BACKFILL videos are returned, to avoid backfilling the whole
    channel history on day one.
    """
    cfg = database.get_podcast_config()
    channel_url = cfg.get("channel_url") or "https://www.youtube.com/@shengfm/videos"
    channel_id = resolve_channel_id(channel_url)

    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    root = ElementTree.fromstring(_http_get(feed_url))
    ns = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}

    known = database.get_known_video_ids()
    is_first_run = not database.has_any_episode()

    videos: list[dict] = []
    for entry in root.findall("atom:entry", ns):
        video_id_el = entry.find("yt:videoId", ns)
        title_el = entry.find("atom:title", ns)
        published_el = entry.find("atom:published", ns)
        video_id = video_id_el.text if video_id_el is not None else None
        if not video_id or video_id in known:
            continue
        videos.append({
            "video_id": video_id,
            "channel_id": channel_id,
            "title": title_el.text if title_el is not None else video_id,
            "published_at": published_el.text if published_el is not None else None,
            "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        })

    if is_first_run:
        videos = videos[:FIRST_RUN_BACKFILL]
    return videos


# ---------------------------------------------------------------------------
# Transcript download (yt-dlp, metadata only — skip_download=True)
# ---------------------------------------------------------------------------

def _pick_caption_track(subs_by_lang: dict) -> list[dict] | None:
    """Given a {lang: [{ext, url}, ...]} dict, return the track list for the
    first matching Chinese lang candidate, preferring json3 format within it."""
    for lang in _ZH_LANG_CANDIDATES:
        for key, tracks in subs_by_lang.items():
            if key == lang or key.startswith(lang + "-"):
                return tracks
    return None


def _json3_to_text(data: dict) -> str:
    """Flatten a YouTube json3 caption payload into plain text, de-duplicating
    the rolling/incremental lines automatic captions produce (each event often
    repeats-and-extends the previous one as the caption scrolls)."""
    lines: list[str] = []
    prev = ""
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(seg.get("utf8", "") for seg in segs).strip()
        text = text.replace("\n", " ").strip()
        if not text:
            continue
        if text.startswith(prev) and prev:
            # Rolling caption growing incrementally — this event supersedes
            # the previous partial line, don't emit prev as a separate line.
            prev = text
            if lines:
                lines[-1] = text
            else:
                lines.append(text)
            continue
        if prev and prev in text:
            lines[-1] = text
            prev = text
            continue
        lines.append(text)
        prev = text
    return " ".join(lines)


def fetch_transcript(video_id: str) -> tuple[str | None, dict]:
    """Download the Chinese transcript for a video via yt-dlp (metadata only,
    no video/audio download). Manual captions are preferred over automatic
    ones. Returns (transcript_text_or_None, meta) — meta always has at least
    'title'. transcript is None when no Chinese captions exist at all
    (caller stores status='no_transcript').
    """
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {"skip_download": True, "quiet": True, "no_warnings": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    meta = {"title": info.get("title") or video_id, "upload_date": info.get("upload_date")}

    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    tracks = _pick_caption_track(manual) or _pick_caption_track(auto)
    if not tracks:
        logger.info("podcast: no Chinese captions for %s", video_id)
        return None, meta

    # Prefer json3 (structured, easy to dedupe); fall back to any available format.
    track = next((t for t in tracks if t.get("ext") == "json3"), tracks[0])
    try:
        raw = _http_get(track["url"], timeout=30)
        if track.get("ext") == "json3":
            transcript = _json3_to_text(json.loads(raw))
        else:
            # Best-effort plain-text fallback for non-json3 formats (vtt/srv3):
            # strip tags/timestamps, keep the rest.
            text = raw.decode("utf-8", errors="replace")
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"^\s*\d+\s*$", "", text, flags=re.MULTILINE)
            text = re.sub(r"\d{2}:\d{2}:\d{2}[.,]\d{3}.*", "", text)
            transcript = re.sub(r"\s+", " ", text).strip()
    except (urllib.error.URLError, json.JSONDecodeError, KeyError) as e:
        logger.warning("podcast: caption download failed for %s: %s", video_id, e)
        return None, meta

    return (transcript or None), meta


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

def summarize(transcript: str, title: str, detail_level: str) -> dict:
    """Thin wrapper over ai.summarize_podcast_transcript — kept here so
    podcast.py's public surface is self-contained (run_check only imports
    from this module + database)."""
    return ai.summarize_podcast_transcript(transcript, title, detail_level)


def filter_new_words(words: list[dict]) -> list[dict]:
    """Drop words the AI picked that are already in entries.word_zh — Daniel
    already has those in his SRS deck, no need to flag them again."""
    if not words:
        return []
    existing = database.word_zh_exists([w["word"] for w in words])
    return [w for w in words if w["word"] not in existing]


# ---------------------------------------------------------------------------
# Spotify link
# ---------------------------------------------------------------------------

def _spotify_search_fallback(title: str) -> str:
    return f"https://open.spotify.com/search/{urllib.parse.quote(title)}"


def find_spotify_url(title: str) -> str:
    """Look up a Spotify episode link for `title` via the Web API's
    client-credentials flow when SPOTIFY_CLIENT_ID/SECRET are configured;
    otherwise (or on any failure) fall back to a search link."""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not client_id or not client_secret:
        return _spotify_search_fallback(title)

    try:
        auth = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        token_req = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=b"grant_type=client_credentials",
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(token_req, timeout=15) as resp:
            token = json.loads(resp.read())["access_token"]

        q = urllib.parse.urlencode({"type": "episode", "market": "DE", "q": title, "limit": 1})
        search_req = urllib.request.Request(
            f"https://api.spotify.com/v1/search?{q}",
            headers={"Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(search_req, timeout=15) as resp:
            data = json.loads(resp.read())
        items = (data.get("episodes") or {}).get("items") or []
        if items:
            url = items[0].get("external_urls", {}).get("spotify")
            if url:
                return url
    except Exception as e:
        logger.warning("podcast: Spotify lookup failed for %r: %s", title, e)

    return _spotify_search_fallback(title)


# ---------------------------------------------------------------------------
# Email notification
# ---------------------------------------------------------------------------

def _words_table_html(words: list[dict]) -> str:
    if not words:
        return "<p><em>Keine neuen HSK5+ Vokabeln gefunden.</em></p>"
    rows = "".join(
        f"<tr><td style='padding:4px 12px 4px 0'>{w['word']}</td>"
        f"<td style='padding:4px 12px 4px 0;color:#666'>{w.get('pinyin', '')}</td>"
        f"<td style='padding:4px 0'>{w.get('definition_de', '')}</td></tr>"
        for w in words
    )
    return (
        "<table style='border-collapse:collapse'>"
        "<tr><th align='left'>Wort</th><th align='left'>Pinyin</th><th align='left'>Bedeutung</th></tr>"
        f"{rows}</table>"
    )


def send_email(episode: dict) -> bool:
    """Send the HTML notification email for a freshly-summarized episode.
    Returns True if sent, False if skipped (SMTP not configured) — skipping
    is not an error, callers just don't set email_sent_at."""
    host = os.environ.get("SMTP_HOST")
    username = os.environ.get("SMTP_USERNAME")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("SMTP_FROM") or username
    if not host or not username or not password or not from_addr:
        logger.info("podcast: SMTP not configured, skipping email for %s", episode["video_id"])
        return False

    port = int(os.environ.get("SMTP_PORT", "587"))
    public_base = os.environ.get("PUBLIC_BASE_URL", "https://powerdaniel3000.duckdns.org")
    cfg = database.get_podcast_config()
    to_addr = cfg.get("email_to") or "u82g@outlook.com"

    transcript_link = f"{public_base}/#podcast-{episode['id']}"
    words_html = _words_table_html(episode.get("hsk_words") or [])

    html = f"""
    <html><body style="font-family:sans-serif;max-width:640px">
      <h2>{episode['title']}</h2>
      <div>{episode.get('summary_de') or ''}</div>
      <h3>Neue HSK5+ Vokabeln</h3>
      {words_html}
      <p>
        <a href="{transcript_link}">Transkript ansehen</a> ·
        <a href="{episode.get('spotify_url') or ''}">Spotify</a> ·
        <a href="{episode['youtube_url']}">YouTube</a>
      </p>
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Neue Podcast-Folge: {episode['title']}"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(username, password)
        server.sendmail(from_addr, [to_addr], msg.as_string())

    logger.info("podcast: email sent for %s to %s", episode["video_id"], to_addr)
    return True


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_check() -> dict:
    """Run one full crawl cycle: discover new videos, fetch transcripts,
    summarize, email. Never lets one episode's failure abort the rest — each
    is wrapped so a bad transcript/AI hiccup just marks that episode 'error'.

    Returns a summary dict: {new, summarized, emailed, failed, skipped}.
    """
    cfg = database.get_podcast_config()
    if cfg.get("enabled", "1") not in ("1", "true", "True"):
        logger.info("podcast: disabled via config, skipping check")
        return {"new": 0, "summarized": 0, "emailed": 0, "failed": 0, "skipped": True}

    from routes.utils import DISABLE_AI

    detail_level = cfg.get("detail_level") or "detailed"
    summary = {"new": 0, "summarized": 0, "emailed": 0, "failed": 0, "skipped": False}

    try:
        new_videos = fetch_new_videos()
    except Exception as e:
        logger.error("podcast: fetch_new_videos failed: %s", e)
        summary["failed"] += 1
        summary["error"] = str(e)
        return summary

    for video in new_videos:
        episode_id = database.create_pending_episode(
            video["video_id"], video["channel_id"], video["title"],
            video["published_at"], video["youtube_url"],
        )
        summary["new"] += 1
        try:
            transcript, meta = fetch_transcript(video["video_id"])
            if not transcript:
                database.update_episode(episode_id, status="no_transcript")
                continue
            database.update_episode(episode_id, transcript_zh=transcript)

            if DISABLE_AI:
                # Dev mode: stop at pending with the transcript stored, no AI
                # call, no email — matches DISABLE_AI's behavior for stories.
                continue

            result = summarize(transcript, video["title"], detail_level)
            if not result.get("summary_de"):
                database.update_episode(episode_id, status="error",
                                        error="AI summary failed or empty")
                summary["failed"] += 1
                continue

            words = filter_new_words(result.get("words") or [])
            spotify_url = find_spotify_url(video["title"])
            database.update_episode(
                episode_id,
                summary_de=result["summary_de"],
                hsk_words=words,
                detail_level=detail_level,
                spotify_url=spotify_url,
                status="summarized",
            )
            summary["summarized"] += 1

            episode = database.get_episode(episode_id)
            try:
                sent = send_email(episode)
            except Exception as e:
                # An SMTP hiccup must not downgrade a successfully summarized
                # episode to 'error' — the summary is stored and viewable on
                # the website regardless; only email_sent_at stays unset.
                logger.warning("podcast: email failed for %s: %s", video["video_id"], e)
                sent = False
            if sent:
                from datetime import datetime
                database.update_episode(episode_id, email_sent_at=datetime.now().isoformat())
                summary["emailed"] += 1
        except Exception as e:
            logger.error("podcast: episode %s failed: %s", video["video_id"], e)
            database.update_episode(episode_id, status="error", error=str(e))
            summary["failed"] += 1

    return summary
