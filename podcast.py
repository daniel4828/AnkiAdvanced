"""Podcast crawler (issue #479): discover new videos on a YouTube channel,
download the Chinese transcript, summarize into German + HSK5+ vocabulary via
AI, find a Spotify link, and email a notification.

Style follows news_fetcher.py: mostly-pure functions + logger, one module for
the whole pipeline. The one heavier dependency is yt-dlp (requirements.txt),
used purely for metadata + subtitle URL extraction (skip_download=True — no
video/audio is ever downloaded), except when captions are missing, in which
case an audio track is downloaded (and always deleted) and transcribed via
NotebookLM (free, #486, primary) or OpenAI Whisper (paid, #485, fallback).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import smtplib
import subprocess
import tempfile
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

# Audio transcription (#485 Whisper, #486 NotebookLM) cost/time guardrails.
# @shengfm has captions disabled entirely, so this is the only way to get a
# transcript for that channel — audio downloads + transcription take time
# (and, for Whisper, real money), so we refuse anything absurdly long and
# keep Whisper segments small. This guardrail applies to the shared
# download/transcode step, so it protects both transcription paths.
_AUDIO_MAX_SECONDS = 3 * 60 * 60  # 3h — guards against a mislabeled/huge video
_WHISPER_SEGMENT_SECONDS = 20 * 60  # 20min segments stay well under OpenAI's 25MB upload cap

# NotebookLM (#486) settings. The notebook is created once and its id cached
# in podcast_config so every episode's audio lands in the same place; the
# source is deleted right after we read its fulltext so the notebook never
# grows unbounded. notebooklm-py is unofficial (undocumented Google RPCs) and
# has no documented per-source size cap; our mp3s are 16kHz/mono/32kbps, so
# even a full 3h episode (the guardrail above) is only ~43MB — the ceiling
# below is a generous safety net, not a known Google limit.
NOTEBOOKLM_NOTEBOOK_TITLE = "AnkiAdvanced Transcripts"
_NOTEBOOKLM_INDEX_TIMEOUT = 10 * 60  # 10min cap for source indexing to finish
_NOTEBOOKLM_MAX_UPLOAD_BYTES = 190 * 1024 * 1024

# Whisper (#485) is real money, so it only runs for short episodes (#495):
# duration <= whisper_max_minutes. The earlier title filter ("早咖啡", #486)
# never matched real episode titles and is retired (the config key is
# ignored). NotebookLM (free) is not subject to this gate.
_DEFAULT_WHISPER_MAX_MINUTES = 30


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# Repo root (this file's directory) — used to resolve the default cookie file
# path so it works regardless of the process cwd (systemd, cron, dev shell).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _ydl_opts(**extra) -> dict:
    """Build the yt-dlp options dict shared by every call site: quiet flags
    plus an optional Netscape cookie file (#491 — YouTube blocks datacenter
    IPs with "Sign in to confirm you're not a bot"; cookies from a logged-in
    browser get around that). The path comes from $YT_DLP_COOKIES (default
    data/yt_cookies.txt; relative paths are resolved against the repo root)
    and is silently omitted when the file doesn't exist, so local development
    without a cookie file behaves exactly as before. Export/refresh
    instructions live in scripts/README.md.
    """
    opts: dict = {"quiet": True, "no_warnings": True}
    cookie_path = os.environ.get("YT_DLP_COOKIES") or os.path.join("data", "yt_cookies.txt")
    if not os.path.isabs(cookie_path):
        cookie_path = os.path.join(_BASE_DIR, cookie_path)
    if os.path.isfile(cookie_path):
        opts["cookiefile"] = cookie_path
    opts.update(extra)
    return opts


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


def _download_audio(video_id: str, tmp_dir: str) -> tuple[str, float]:
    """Download the lowest-bitrate audio track for `video_id` with yt-dlp and
    transcode it to a single 16kHz mono ~32kbps mp3 with ffmpeg. Shared by
    both the NotebookLM (#486) and Whisper (#485) transcription paths so the
    (slow) download+transcode only ever happens once per episode.

    Returns (mp3_path, duration_seconds), with mp3_path inside tmp_dir (the
    caller owns tmp_dir's lifetime/cleanup). Raises ValueError if duration
    exceeds the cost guardrail, RuntimeError on yt-dlp/ffmpeg failures.
    """
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = _ydl_opts(
        format="worstaudio[abr>=32]/worstaudio",
        outtmpl=os.path.join(tmp_dir, "audio.%(ext)s"),
    )
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    duration = info.get("duration") or 0
    if duration > _AUDIO_MAX_SECONDS:
        raise ValueError(
            f"podcast: audio for {video_id} is {duration / 3600:.1f}h, exceeds the "
            f"{_AUDIO_MAX_SECONDS / 3600:.0f}h audio transcription cost guardrail"
        )

    downloaded = next((f for f in os.listdir(tmp_dir) if f.startswith("audio.")), None)
    if not downloaded:
        raise RuntimeError(f"podcast: yt-dlp did not produce an audio file for {video_id}")
    src_path = os.path.join(tmp_dir, downloaded)

    mp3_path = os.path.join(tmp_dir, "full.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ar", "16000", "-ac", "1", "-b:a", "32k",
        mp3_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"podcast: ffmpeg failed for {video_id}: {result.stderr[-500:]}")

    return mp3_path, duration


def _split_audio_segments(mp3_path: str, tmp_dir: str, duration: float) -> list[str]:
    """Split the already-transcoded mp3 into <=_WHISPER_SEGMENT_SECONDS chunks
    for Whisper's per-request upload cap. Uses stream copy (-c copy, no
    re-encode) since the source is already 16kHz/mono/32kbps. Returns the
    single mp3_path unchanged if it's already short enough."""
    if duration <= _WHISPER_SEGMENT_SECONDS:
        return [mp3_path]

    cmd = [
        "ffmpeg", "-y", "-i", mp3_path,
        "-c", "copy", "-f", "segment", "-segment_time", str(_WHISPER_SEGMENT_SECONDS),
        os.path.join(tmp_dir, "seg_%03d.mp3"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"podcast: ffmpeg segmenting failed: {result.stderr[-500:]}")

    segments = sorted(f for f in os.listdir(tmp_dir) if f.startswith("seg_") and f.endswith(".mp3"))
    if not segments:
        raise RuntimeError("podcast: ffmpeg produced no audio segments")
    return [os.path.join(tmp_dir, s) for s in segments]


def _transcribe_via_whisper(mp3_path: str, duration: float, video_id: str, tmp_dir: str) -> str | None:
    """Paid fallback (#485): segment the shared mp3 and transcribe each
    segment via OpenAI's audio.transcriptions endpoint.

    Returns None when OPENAI_API_KEY is simply missing (config choice, not a
    failure). Raises on actual transcription failure; callers log and treat
    that the same as no transcript.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        logger.warning("podcast: OPENAI_API_KEY not set, skipping Whisper for %s", video_id)
        return None

    segments = _split_audio_segments(mp3_path, tmp_dir, duration)

    import openai
    client = openai.OpenAI()
    texts: list[str] = []
    for seg_path in segments:
        seg_name = os.path.basename(seg_path)
        text = None
        for attempt in range(2):  # one retry on transient failure
            try:
                with open(seg_path, "rb") as f:
                    resp = client.audio.transcriptions.create(
                        model="gpt-4o-mini-transcribe", file=f, language="zh",
                    )
                text = resp.text
                break
            except Exception as e:
                logger.warning(
                    "podcast: Whisper transcription failed (attempt %d) for %s/%s: %s",
                    attempt + 1, video_id, seg_name, e,
                )
        if text is None:
            raise RuntimeError(f"podcast: Whisper transcription failed twice for {video_id}/{seg_name}")
        texts.append(text.strip())

    transcript = " ".join(t for t in texts if t)
    logger.info(
        "podcast: Whisper transcribed %s (%d segment(s), %.0fmin)",
        video_id, len(segments), duration / 60,
    )
    return transcript or None


async def _get_or_create_notebooklm_notebook(client) -> str:
    """Return the id of the dedicated 'AnkiAdvanced Transcripts' notebook,
    reusing the id cached in podcast_config when it still resolves; creates
    (and re-caches) a fresh one otherwise (first run, or the cached notebook
    was deleted server-side)."""
    cfg = database.get_podcast_config()
    cached_id = cfg.get("notebooklm_notebook_id")
    if cached_id:
        notebook = await client.notebooks.get_or_none(cached_id)
        if notebook is not None:
            return notebook.id

    notebook = await client.notebooks.create(NOTEBOOKLM_NOTEBOOK_TITLE)
    database.set_podcast_config("notebooklm_notebook_id", notebook.id)
    return notebook.id


async def _run_notebooklm_transcription(audio_path: str, video_id: str) -> str | None:
    import notebooklm

    async with notebooklm.NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebooklm_notebook(client)
        source = await client.sources.add_file(
            notebook_id, audio_path, title=f"podcast-{video_id}",
        )
        try:
            await client.sources.wait_until_ready(
                notebook_id, source.id, timeout=_NOTEBOOKLM_INDEX_TIMEOUT,
            )
            fulltext = await client.sources.get_fulltext(notebook_id, source.id)
            return fulltext.content or None
        finally:
            # Always drop the source afterwards so the notebook doesn't grow
            # unbounded — a delete failure here must not mask a successful
            # transcription, just log and move on.
            try:
                await client.sources.delete(notebook_id, source.id)
            except Exception as e:
                logger.warning("podcast: failed to delete NotebookLM source for %s: %s", video_id, e)


def _transcribe_via_notebooklm(audio_path: str, video_id: str) -> str | None:
    """Free primary transcription path (#486): upload the shared mp3 to a
    dedicated NotebookLM notebook, wait for indexing, read the source's
    fulltext, then delete the source. Uses the unofficial notebooklm-py
    client (one-time browser login on Daniel's machine, credentials copied to
    the server — see scripts/README.md).

    This is an undocumented, unofficial API that can break at any time, so
    every failure mode (package not installed, not authenticated, RPC error,
    indexing timeout, ...) logs and returns None instead of raising —
    fetch_transcript then falls back to Whisper (or gives up, per
    podcast_config.transcriber).
    """
    try:
        import notebooklm  # noqa: F401 (import-only availability check)
    except ImportError:
        logger.info("podcast: notebooklm-py not installed, skipping NotebookLM for %s", video_id)
        return None

    size = os.path.getsize(audio_path)
    if size > _NOTEBOOKLM_MAX_UPLOAD_BYTES:
        logger.warning(
            "podcast: audio for %s is %.0fMB, exceeds the NotebookLM upload guardrail, skipping",
            video_id, size / 1024 / 1024,
        )
        return None

    try:
        transcript = asyncio.run(_run_notebooklm_transcription(audio_path, video_id))
    except FileNotFoundError:
        # AuthTokens.from_storage() raises this when no credentials file
        # exists yet — i.e. `notebooklm login` was never run. Not an error.
        logger.info("podcast: NotebookLM not authenticated (no credentials file), skipping for %s", video_id)
        return None
    except Exception as e:
        logger.warning("podcast: NotebookLM transcription failed for %s: %s", video_id, e)
        return None

    if transcript:
        logger.info("podcast: NotebookLM transcribed %s (%d chars)", video_id, len(transcript))
    return transcript


def _resolve_transcriber(cfg: dict) -> str:
    """Normalize podcast_config into one of auto|notebooklm|whisper|off.

    Reads the current `transcriber` key when set to a legal value; otherwise
    falls back to the legacy `whisper_fallback` key (#485) so pre-#486
    installs keep behaving the same way without a data migration:
    whisper_fallback=0 -> off, anything else -> auto (NotebookLM first, then
    Whisper — the new default behavior, strictly better than the old
    Whisper-only fallback since NotebookLM is free).
    """
    val = cfg.get("transcriber")
    if val in ("auto", "notebooklm", "whisper", "off"):
        return val
    if cfg.get("whisper_fallback", "1") not in ("1", "true", "True"):
        return "off"
    return "auto"


def _whisper_duration_allowed(duration: float, cfg: dict) -> bool:
    """Whisper costs real money, so it's gated to short episodes:
    duration <= podcast_config.whisper_max_minutes (default 30). Daniel's
    早咖啡-style daily episodes run 10-15 minutes; the long shows he doesn't
    want to pay for run 60-90. Duration replaces the earlier title filter
    (#486) because real episode titles never contain "早咖啡" (issue #495).
    0/empty disables the gate. NotebookLM (free) is never subject to it."""
    raw = cfg.get("whisper_max_minutes", str(_DEFAULT_WHISPER_MAX_MINUTES))
    try:
        max_minutes = float(raw)
    except (TypeError, ValueError):
        max_minutes = _DEFAULT_WHISPER_MAX_MINUTES
    if max_minutes <= 0:
        return True
    return duration <= max_minutes * 60


def fetch_transcript(video_id: str) -> tuple[str | None, dict]:
    """Download the Chinese transcript for a video via yt-dlp (metadata only,
    no video/audio download). Manual captions are preferred over automatic
    ones. Returns (transcript_text_or_None, meta) — meta always has at least
    'title' and 'transcript_source' (one of 'captions'/'notebooklm'/'whisper'/
    None). transcript is None when no Chinese captions exist AND the
    transcription chain (NotebookLM #486 -> Whisper #485, per
    podcast_config.transcriber) is disabled/unavailable/fails for this
    episode (caller stores status='no_transcript' in that case).
    """
    import yt_dlp

    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = _ydl_opts(skip_download=True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    title = info.get("title") or video_id
    meta = {"title": title, "upload_date": info.get("upload_date"), "transcript_source": None}

    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    tracks = _pick_caption_track(manual) or _pick_caption_track(auto)
    transcript = None
    if tracks:
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
            transcript = None
    else:
        logger.info("podcast: no Chinese captions for %s", video_id)

    if transcript:
        meta["transcript_source"] = "captions"
        logger.info("podcast: transcript source for %s = captions", video_id)
        return transcript, meta

    # No usable captions (missing or download failed) — try the audio
    # transcription chain: NotebookLM (free, #486) first, then Whisper (paid,
    # #485), per podcast_config.transcriber.
    cfg = database.get_podcast_config()
    transcriber = _resolve_transcriber(cfg)
    if transcriber == "off":
        logger.info("podcast: transcriber=off, skipping audio transcription for %s", video_id)
        return None, meta

    from routes.utils import DISABLE_AI
    if DISABLE_AI:
        # Dev mode must never trigger audio download/transcription (NotebookLM
        # is free but still an external side effect; Whisper costs money).
        logger.info("podcast: DISABLE_AI set, skipping audio transcription for %s", video_id)
        return None, meta

    if not shutil.which("ffmpeg"):
        logger.warning("podcast: ffmpeg not found, skipping audio transcription for %s", video_id)
        return None, meta

    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mp3_path, duration = _download_audio(video_id, tmp_dir)

            if transcriber in ("auto", "notebooklm"):
                transcript = _transcribe_via_notebooklm(mp3_path, video_id)
                if transcript:
                    meta["transcript_source"] = "notebooklm"
                    logger.info("podcast: transcript source for %s = notebooklm", video_id)
                    return transcript, meta
                if transcriber == "notebooklm":
                    logger.info("podcast: transcriber=notebooklm and NotebookLM failed, not trying Whisper for %s", video_id)
                    return None, meta

            # transcriber in ("auto", "whisper") at this point.
            if not _whisper_duration_allowed(duration, cfg):
                logger.info(
                    "podcast: %s is %.0fmin, over whisper_max_minutes — skipping Whisper",
                    video_id, duration / 60,
                )
                return None, meta

            transcript = _transcribe_via_whisper(mp3_path, duration, video_id, tmp_dir)
            if transcript:
                meta["transcript_source"] = "whisper"
                logger.info("podcast: transcript source for %s = whisper", video_id)
                return transcript, meta
    except Exception as e:
        logger.warning("podcast: audio transcription pipeline failed for %s: %s", video_id, e)
        return None, meta

    logger.info("podcast: transcript source for %s = none (all paths exhausted)", video_id)
    return None, meta


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

# How far back run_check's automatic retry pass (#491) looks: episodes with
# status='error' created within this many days get one re-attempt per cycle.
# The window keeps a permanently-broken video from burning transcription
# money forever (the Whisper duration gate is the other cost guardrail).
_AUTO_RETRY_MAX_AGE_DAYS = 7

# At most this many auto-retries per cycle (#495): a large error backlog
# (15 episodes after the cookie outage) times NotebookLM's 10-minute indexing
# ceiling would make one cycle run for hours; capping it lets the hourly cron
# chew through the backlog a few episodes at a time. Oldest first.
_AUTO_RETRY_PER_CYCLE = 3

# Cross-process lock (#495) so a slow run (audio downloads + transcription
# can exceed an hour) never overlaps the next hourly cron or a manual
# POST /api/podcast/check. fcntl is POSIX-only — fine, prod is Linux and
# dev is macOS.
_RUN_LOCK_PATH = os.path.join(_BASE_DIR, "data", "podcast_check.lock")


def _process_episode(episode_id: int, video: dict, detail_level: str, summary: dict) -> None:
    """Process one already-inserted episode end-to-end: transcript -> AI
    summary -> HSK word filter -> Spotify link -> store -> email. Shared by
    run_check (new videos + the auto-retry pass) and the manual retry
    endpoint (#491). `video` only needs 'video_id' and 'title'.

    Never raises — any unexpected failure marks the episode 'error' and bumps
    summary['failed'] (a missing transcript is 'no_transcript', not a
    failure); success bumps summary['summarized'] / summary['emailed'].
    """
    from routes.utils import DISABLE_AI

    try:
        transcript, meta = fetch_transcript(video["video_id"])
        if not transcript:
            database.update_episode(episode_id, status="no_transcript")
            return
        database.update_episode(
            episode_id, transcript_zh=transcript,
            transcript_source=meta.get("transcript_source"),
        )

        if DISABLE_AI:
            # Dev mode: stop at pending with the transcript stored, no AI
            # call, no email — matches DISABLE_AI's behavior for stories.
            return

        result = summarize(transcript, video["title"], detail_level)
        if not result.get("summary_de"):
            database.update_episode(episode_id, status="error",
                                    error="AI summary failed or empty")
            summary["failed"] += 1
            return

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


def retry_episode(episode_id: int) -> dict:
    """Re-run the full processing pipeline for one existing episode (#491) —
    used by POST /api/podcast/episodes/{id}/retry after e.g. an expired
    YouTube cookie failed a whole batch. Reuses the existing row (video_id is
    UNIQUE — no second INSERT) after resetting its status/error, so a retry
    that fails again just lands back on 'error' with the fresh message.

    The caller (the route) validates that the episode exists and its status
    is retryable. Returns {status, transcript_source, error, emailed} read
    back from the row after processing.
    """
    episode = database.get_episode(episode_id)
    if not episode:
        raise ValueError(f"podcast: episode {episode_id} not found")

    cfg = database.get_podcast_config()
    detail_level = cfg.get("detail_level") or "detailed"
    database.update_episode(episode_id, status="pending", error=None)

    summary = {"summarized": 0, "emailed": 0, "failed": 0}
    video = {"video_id": episode["video_id"], "title": episode["title"]}
    _process_episode(episode_id, video, detail_level, summary)

    fresh = database.get_episode(episode_id)
    return {
        "status": fresh["status"],
        "transcript_source": fresh.get("transcript_source"),
        "error": fresh.get("error"),
        "emailed": summary["emailed"] > 0,
    }


def run_check() -> dict:
    """Run one full crawl cycle: discover new videos, fetch transcripts,
    summarize, email. Never lets one episode's failure abort the rest — each
    is wrapped so a bad transcript/AI hiccup just marks that episode 'error'.
    At the end, recent failures (status='error', created within the last
    _AUTO_RETRY_MAX_AGE_DAYS days) each get one automatic re-attempt (#491),
    so a fixed cookie/network issue heals old failures without manual work.

    Returns a summary dict: {new, summarized, emailed, failed, retried, skipped}.
    """
    import fcntl

    cfg = database.get_podcast_config()
    if cfg.get("enabled", "1") not in ("1", "true", "True"):
        logger.info("podcast: disabled via config, skipping check")
        return {"new": 0, "summarized": 0, "emailed": 0, "failed": 0,
                "retried": 0, "skipped": True}

    # Non-blocking cross-process lock (#495): if another run is still going
    # (transcribing a backlog can take over an hour), skip this cycle instead
    # of processing the same episodes twice in parallel.
    os.makedirs(os.path.dirname(_RUN_LOCK_PATH), exist_ok=True)
    lock_file = open(_RUN_LOCK_PATH, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        logger.info("podcast: another check is already running, skipping this cycle")
        return {"new": 0, "summarized": 0, "emailed": 0, "failed": 0,
                "retried": 0, "skipped": True}
    try:
        return _run_check_locked(cfg)
    finally:
        lock_file.close()


def _run_check_locked(cfg: dict) -> dict:

    detail_level = cfg.get("detail_level") or "detailed"
    summary = {"new": 0, "summarized": 0, "emailed": 0, "failed": 0,
               "retried": 0, "skipped": False}

    try:
        new_videos = fetch_new_videos()
    except Exception as e:
        logger.error("podcast: fetch_new_videos failed: %s", e)
        summary["failed"] += 1
        summary["error"] = str(e)
        return summary

    processed_ids: set[int] = set()
    for video in new_videos:
        episode_id = database.create_pending_episode(
            video["video_id"], video["channel_id"], video["title"],
            video["published_at"], video["youtube_url"],
        )
        summary["new"] += 1
        processed_ids.add(episode_id)
        _process_episode(episode_id, video, detail_level, summary)

    # Auto-retry pass (#491): give recent failures one more chance per cycle,
    # capped at _AUTO_RETRY_PER_CYCLE oldest-first (#495) so a big backlog is
    # chewed through gradually instead of one multi-hour run. Episodes that
    # just failed above are skipped — retrying immediately in the same cycle
    # would almost certainly fail the same way (and double the transcription
    # cost); they'll be picked up on the next cron run instead.
    retryable = [ep for ep in database.list_recent_error_episodes(max_age_days=_AUTO_RETRY_MAX_AGE_DAYS)
                 if ep["id"] not in processed_ids]
    for ep in retryable[:_AUTO_RETRY_PER_CYCLE]:
        logger.info("podcast: auto-retrying failed episode %s (%s)", ep["id"], ep["video_id"])
        summary["retried"] += 1
        database.update_episode(ep["id"], status="pending", error=None)
        _process_episode(ep["id"], {"video_id": ep["video_id"], "title": ep["title"]},
                         detail_level, summary)

    return summary
