"""Podcast crawler (issue #479): discover new episodes from podcast RSS
feeds, transcribe them, summarize into German + HSK5+ vocabulary via AI,
find a Spotify link, and email a notification.

Style follows news_fetcher.py: mostly-pure functions + logger, one module for
the whole pipeline.

Source (#497, feeds moved to the podcast_feeds table in #502): plain public
RSS feeds — the original YouTube-channel source (#479) was retired because
YouTube started bot-verifying the server's datacenter IP with no durable
Cookie fix (#491). RSS gives a direct MP3 enclosure link, so there is no
audio *download* step for the primary transcription path (Tingwu, #498)
at all — only the paid/optional fallbacks (Whisper #485, NotebookLM #486)
still need the audio downloaded+transcoded locally, via plain urllib (no
more yt-dlp).
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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import ai
import database

logger = logging.getLogger(__name__)

# On a feed's very first crawl (no episodes from that feed in the DB yet)
# only backfill this many of its most recent episodes — otherwise
# subscribing to an established feed would try to transcribe/summarize its
# entire back catalog in one cycle (#497). Backfilled episodes are never
# auto-processed (#502, see _run_check_locked's is_backfill check) — they're
# metadata-only rows the UI lists for manual transcription — so this can be
# generous (10, was 3 pre-#502) without risking a burst of paid/slow
# transcription work on a freshly-added source.
FIRST_RUN_BACKFILL = 10

# itunes:duration namespace, used to read episode duration straight from the
# RSS feed (#497) — this is what lets duration-based guardrails/gates run
# *before* any download.
_ITUNES_NS = {"itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}

# Audio transcription (#485 Whisper, #486 NotebookLM, #498 Tingwu) cost/time
# guardrails. Whisper/NotebookLM segments stay small; this guardrail applies
# to the shared download/transcode step, so it protects both paid/optional
# fallback paths. Tingwu (primary) is submitted as a direct URL — Alibaba
# does its own duration limiting server-side — but the same 3h check is
# applied up front (before any transcriber runs) since RSS gives us the
# duration for free.
_AUDIO_MAX_SECONDS = 3 * 60 * 60  # 3h — guards against a mislabeled/huge episode
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
# ignored). NotebookLM (free) is not subject to this gate. Tingwu (#498,
# primary) isn't subject to it either — it's cheaper than Whisper per hour.
_DEFAULT_WHISPER_MAX_MINUTES = 30

# Tongyi Tingwu (通义听悟, #498) offline transcription task polling. Tasks
# for a 15-90min podcast episode typically finish in a few minutes; 20min at
# 15s intervals is a generous ceiling before giving up and falling back.
_TINGWU_ENDPOINT = "tingwu.cn-beijing.aliyuncs.com"
_TINGWU_REGION = "cn-beijing"
_TINGWU_POLL_INTERVAL_SECONDS = 15
_TINGWU_POLL_TIMEOUT_SECONDS = 20 * 60


def _http_get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# Repo root (this file's directory) — kept around for path resolution
# (e.g. the run-lock file below).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Episode discovery (RSS, #497)
# ---------------------------------------------------------------------------

def _parse_itunes_duration(raw: str | None) -> int | None:
    """Parse an itunes:duration value into whole seconds. The iTunes podcast
    spec allows plain seconds, MM:SS or H:MM:SS — real feeds use all three
    (Daniel's two feeds alone mix MM:SS and H:MM:SS)."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        parts = [int(p) for p in raw.split(":")]
    except ValueError:
        return None
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


def _parse_rss_pubdate(raw: str | None) -> str | None:
    """RSS <pubDate> (RFC 822) -> ISO 8601, matching the format previously
    stored from YouTube's Atom <published> (which was already ISO)."""
    if not raw:
        return None
    try:
        return parsedate_to_datetime(raw).isoformat()
    except (TypeError, ValueError):
        return raw  # best-effort: keep the raw string rather than lose it


def fetch_new_videos() -> list[dict]:
    """Return episodes from the configured podcast RSS feeds
    (podcast_feeds table, #502 — one row per source with its own
    auto_process flag) that aren't in the DB yet. Zero API keys needed —
    plain public RSS/XML, no bot walls.

    Per feed: if that specific feed has zero episodes in the DB yet, only
    its latest FIRST_RUN_BACKFILL episodes are returned (backfill mode,
    marked `is_backfill: True`, see below). Otherwise, RSS items are
    newest-first by convention, so items are walked from the top and
    collection *stops at the first already-known guid* — everything older
    than that was either already ingested or deliberately left out of the
    initial backfill, and must stay left out forever (else every subsequent
    crawl of a long-running feed like 声动早咖啡, which has ~1000
    back-catalog episodes, would dump the *entire* backlog as "new" in one
    shot the very next cycle — this was caught by a real backfill+re-run
    test against both of Daniel's feeds during #497's implementation, not
    just theorized). A feed that fails to fetch/parse is logged and
    skipped — one broken feed must not block the others.

    Each returned dict carries `auto_process` (bool, copied from the feed
    row) and `is_backfill` (bool, True for episodes discovered on a feed's
    very first crawl) — `_run_check_locked` uses both to decide whether to
    immediately transcribe+summarize a newly-discovered episode (#502):
    only non-backfill episodes from an auto_process feed are processed
    automatically; everything else is stored metadata-only for manual
    transcription from the UI.

    If a feed's `title` is still unset (freshly added via the UI, no
    network request made at add-time), it's backfilled here from the RSS
    channel's own <title> element.
    """
    feeds = database.list_feeds()
    if not feeds:
        logger.warning("podcast: no feeds configured (podcast_feeds table)")
        return []

    known = database.get_known_video_ids()

    videos: list[dict] = []
    for feed in feeds:
        feed_url = feed["url"]
        try:
            root = ElementTree.fromstring(_http_get(feed_url, timeout=30))
        except (urllib.error.URLError, ElementTree.ParseError) as e:
            logger.warning("podcast: failed to fetch/parse feed %s: %s", feed_url, e)
            continue

        if not feed.get("title"):
            channel_title_el = root.find("channel/title")
            if channel_title_el is not None and channel_title_el.text:
                database.update_feed(feed["id"], title=channel_title_el.text.strip())

        auto_process = bool(feed.get("auto_process"))
        is_first_run = not database.has_any_episode_for_feed(feed_url)
        feed_videos: list[dict] = []
        for item in root.findall(".//item"):
            enclosure_el = item.find("enclosure")
            audio_url = enclosure_el.get("url") if enclosure_el is not None else None
            guid_el = item.find("guid")
            guid = guid_el.text.strip() if guid_el is not None and guid_el.text else None
            # Fall back to the enclosure URL as the unique id when a feed
            # omits <guid> (not expected for either of Daniel's feeds, but
            # cheap insurance) — never fall back to nothing, we need a
            # stable video_id for the UNIQUE constraint / dedup.
            video_id = guid or audio_url
            if not video_id:
                continue
            if video_id in known:
                if is_first_run:
                    continue  # shouldn't happen (nothing's known yet), but harmless
                break  # newest-first feed: everything from here on is old backlog

            title_el = item.find("title")
            link_el = item.find("link")
            pubdate_el = item.find("pubDate")
            duration_el = item.find("itunes:duration", _ITUNES_NS)
            feed_videos.append({
                "video_id": video_id,
                "channel_id": feed_url,
                "title": (title_el.text.strip() if title_el is not None and title_el.text else video_id),
                "published_at": _parse_rss_pubdate(pubdate_el.text if pubdate_el is not None else None),
                "youtube_url": (link_el.text.strip() if link_el is not None and link_el.text else feed_url),
                "audio_url": audio_url,
                "duration_seconds": _parse_itunes_duration(
                    duration_el.text if duration_el is not None else None),
                "auto_process": auto_process,
                "is_backfill": is_first_run,
            })
            if is_first_run and len(feed_videos) >= FIRST_RUN_BACKFILL:
                break

        videos.extend(feed_videos)

    return videos


# ---------------------------------------------------------------------------
# Audio download (#497: plain urllib from the RSS enclosure URL, no yt-dlp)
# ---------------------------------------------------------------------------

def _download_audio(audio_url: str, video_id: str, tmp_dir: str) -> str:
    """Download the RSS enclosure mp3 directly and transcode it to a single
    16kHz mono ~32kbps mp3 with ffmpeg. Shared by the Whisper (#485) and
    NotebookLM (#486) transcription paths (Tingwu, #498, is primary and
    needs no download at all — it's submitted the audio_url directly) so
    the (slow) download+transcode only ever happens once per episode.

    Returns mp3_path inside tmp_dir (the caller owns tmp_dir's lifetime).
    Duration is not re-derived here — the RSS itunes:duration guardrail
    check already happened in fetch_transcript *before* this is called, per
    issue #497 ("guardrails before download"). Raises RuntimeError on
    download/ffmpeg failures.
    """
    src_path = os.path.join(tmp_dir, "src_audio")
    req = urllib.request.Request(audio_url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(src_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except urllib.error.URLError as e:
        raise RuntimeError(f"podcast: failed to download audio for {video_id}: {e}")

    mp3_path = os.path.join(tmp_dir, "full.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", src_path,
        "-ar", "16000", "-ac", "1", "-b:a", "32k",
        mp3_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"podcast: ffmpeg failed for {video_id}: {result.stderr[-500:]}")

    return mp3_path


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
    # Whisper is billed per minute, not per token. Log the audio duration (in
    # seconds) as input_tokens so database.stats._row_cost can price it via
    # the "per_minute" pricing entry — only on success, since a failed call
    # above already raised before reaching here.
    database.log_api_call(
        model="gpt-4o-mini-transcribe", input_tokens=int(duration),
        output_tokens=0, purpose="podcast-transcribe",
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


def _normalize_transcript(text: str) -> str:
    """Clean up ASR output before storing/summarizing (#500). NotebookLM's
    speech recognition emits Traditional Chinese with a space between every
    character ("用 聲 音  生 動 活 潑") — that breaks jieba segmentation in
    the podcast review mode, HSK word matching against entries.word_zh, and
    wastes prompt tokens. Two steps:
      1. drop whitespace adjacent to any CJK character or full-width
         punctuation (keeps spacing inside pure-Latin runs; "2026 年" ->
         "2026年", "AI 记忆" -> "AI记忆", "活泼。 2026" -> "活泼。2026")
      2. Traditional -> Simplified via zhconv (pure-Python, requirements.txt)
    Tingwu/Whisper output is already Simplified without spacing — running it
    through here is a harmless no-op, so every source is normalized uniformly.
    """
    if not text:
        return text
    text = re.sub(r"(?<=[一-鿿　-〿＀-￯])\s+|\s+(?=[一-鿿　-〿＀-￯])", "", text)
    try:
        from zhconv import convert
        text = convert(text, "zh-cn")
    except ImportError:  # dependency missing (old venv) — spacing fix still applies
        logger.warning("podcast: zhconv not installed, skipping Traditional->Simplified conversion")
    return text


def _notebooklm_credentials_available() -> bool:
    """Best-effort presence check for NotebookLM login credentials (#510),
    used to decide whether the 'auto' transcriber/summarizer chains should
    even attempt the free NotebookLM path first. Not a hard gate — the
    actual calls (_transcribe_via_notebooklm, _summarize_via_notebooklm)
    still handle FileNotFoundError from notebooklm-py itself (e.g.
    credentials revoked after this check ran), same as before #510.

    Mirrors notebooklm-py's own storage_state.json lookup (see
    scripts/README.md): $NOTEBOOKLM_HOME/storage_state.json, or (if a
    profile was used at login) $NOTEBOOKLM_HOME/profiles/<profile>/
    storage_state.json. Split into its own function so tests can monkeypatch
    it directly instead of poking os.path.
    """
    try:
        import notebooklm  # noqa: F401 (import-only availability check)
    except ImportError:
        return False

    home = os.environ.get("NOTEBOOKLM_HOME") or os.path.expanduser("~/.notebooklm")
    if os.path.isfile(os.path.join(home, "storage_state.json")):
        return True
    profiles_dir = os.path.join(home, "profiles")
    if os.path.isdir(profiles_dir):
        for name in os.listdir(profiles_dir):
            if os.path.isfile(os.path.join(profiles_dir, name, "storage_state.json")):
                return True
    return False


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


# NotebookLM sources have no documented per-source size cap; a transcript is
# plain text (much denser than audio), so this defensive ceiling is far more
# generous than any real episode transcript will hit — it just prevents an
# unbounded upload if something ever feeds this a huge string.
_NOTEBOOKLM_SUMMARY_TEXT_MAX_CHARS = 200_000


async def _run_notebooklm_summary(transcript: str, title: str, detail_level: str) -> str | None:
    """Async body of _summarize_via_notebooklm (#510): upload the transcript
    as a text source, wait for indexing, ask the same summary question used
    by the API path (ai.build_podcast_summary_prompt) restricted to that one
    source, then delete the source. Returns the raw answer text (still needs
    ai.parse_podcast_summary_json) or None on any handled failure."""
    import notebooklm

    async with notebooklm.NotebookLMClient.from_storage() as client:
        notebook_id = await _get_or_create_notebooklm_notebook(client)
        source = await client.sources.add_text(
            notebook_id, f"podcast-transcript-{title}",
            transcript[:_NOTEBOOKLM_SUMMARY_TEXT_MAX_CHARS],
        )
        try:
            await client.sources.wait_until_ready(
                notebook_id, source.id, timeout=_NOTEBOOKLM_INDEX_TIMEOUT,
            )
            question = ai.build_podcast_summary_prompt(transcript, title, detail_level)
            result = await client.chat.ask(notebook_id, question, source_ids=[source.id])
            return result.answer or None
        finally:
            # Same reasoning as _run_notebooklm_transcription: never let a
            # delete failure mask a successful summary, just log and move on.
            try:
                await client.sources.delete(notebook_id, source.id)
            except Exception as e:
                logger.warning("podcast: failed to delete NotebookLM summary source for %r: %s", title, e)


def _summarize_via_notebooklm(transcript: str, title: str, detail_level: str) -> dict | None:
    """Free summary path (#510): ask NotebookLM's chat interface to summarize
    the transcript (already-uploaded-and-deleted per episode, so this
    uploads its own throwaway text source) using the exact same prompt/JSON
    contract as the paid API path (ai.summarize_podcast_transcript), so
    downstream code (podcast._process_episode) doesn't care which path ran.

    Same unofficial-API failure posture as _transcribe_via_notebooklm: every
    failure mode (package missing, not authenticated, RPC error, indexing
    timeout, empty/unparseable answer, ...) logs and returns None instead of
    raising — summarize() then falls back to the API chain.
    """
    try:
        import notebooklm  # noqa: F401 (import-only availability check)
    except ImportError:
        logger.info("podcast: notebooklm-py not installed, skipping NotebookLM summary for %r", title)
        return None

    try:
        answer = asyncio.run(_run_notebooklm_summary(transcript, title, detail_level))
    except FileNotFoundError:
        logger.info("podcast: NotebookLM not authenticated (no credentials file), skipping summary for %r", title)
        return None
    except Exception as e:
        logger.warning("podcast: NotebookLM summary failed for %r: %s", title, e)
        return None

    if not answer:
        logger.warning("podcast: NotebookLM summary returned no answer for %r", title)
        return None

    result = ai.parse_podcast_summary_json(answer)
    if not result.get("summary_de"):
        logger.warning("podcast: NotebookLM summary answer was unparseable/empty for %r", title)
        return None

    logger.info("podcast: NotebookLM summarized %r (%d word(s))", title, len(result.get("words") or []))
    return result


def _fmt_timestamp(ms: float) -> str:
    """Milliseconds -> "[MM:SS]" (or "[H:MM:SS]" past the hour) for prefixing
    a transcript paragraph (#543), so the summary AI can cite roughly when a
    topic was discussed."""
    total = int(ms // 1000)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"[{h}:{m:02d}:{s:02d}]" if h else f"[{m:02d}:{s:02d}]"


def _paragraph_start_ms(p: dict) -> int | None:
    """Extract a paragraph's start time in milliseconds from a Tingwu
    paragraph (#543). Tingwu's exact key isn't verified against a real
    response (see _parse_tingwu_transcript), so try the plausible paragraph-
    level keys, then fall back to the first word's start. Returns None when no
    timing is present — the caller then emits that paragraph without a prefix."""
    for key in ("Start", "BeginTime", "StartTime"):
        v = p.get(key)
        if isinstance(v, (int, float)):
            return int(v)
    for w in p.get("Words") or []:
        for key in ("Start", "BeginTime", "StartTime"):
            v = w.get(key)
            if isinstance(v, (int, float)):
                return int(v)
    return None


def _parse_tingwu_transcript(result_json: dict) -> str:
    """Best-effort flatten of the Tingwu offline transcription result JSON
    (fetched from the URL in Result.Transcription once the task completes)
    into plain text, each paragraph prefixed with its start timestamp when
    available (#543).

    The documented shape is Transcription.Paragraphs[], each either carrying
    a Text field directly or a Words[] list of {Text: ...} tokens to join —
    walk both. Falls back to recursively collecting every "Text" string
    found anywhere in the payload if neither matches, so an undocumented/
    changed shape degrades to "some text" instead of an empty transcript
    (this fallback is exercised by the unit tests; the primary shape is
    unverified against a real response since #498 shipped without
    credentials to test with — see CLAUDE.md/scripts/README.md).
    """
    paragraphs = (
        (result_json.get("Transcription") or {}).get("Paragraphs")
        or result_json.get("Paragraphs")
        or []
    )
    lines: list[str] = []
    for p in paragraphs:
        text = p.get("Text")
        if not text:
            words = p.get("Words") or []
            text = "".join(w.get("Text", "") for w in words)
        if not text:
            continue
        start_ms = _paragraph_start_ms(p)
        lines.append(f"{_fmt_timestamp(start_ms)} {text}" if start_ms is not None else text)
    if lines:
        return " ".join(lines)

    collected: list[str] = []

    def _walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "Text" and isinstance(v, str) and v.strip():
                    collected.append(v.strip())
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(result_json)
    return " ".join(collected)


def _transcribe_via_tingwu(audio_url: str, video_id: str) -> str | None:
    """Primary transcription path (#498): submit the RSS enclosure mp3 URL
    directly to Alibaba Cloud's Tongyi Tingwu (通义听悟) offline
    transcription API — no audio download needed at all, official API,
    ~¥0.6/hour (vs Whisper's ~¥1.3/hour), 90-day free tier for new accounts.

    Requires ALIBABA_CLOUD_ACCESS_KEY_ID / ALIBABA_CLOUD_ACCESS_KEY_SECRET
    (the alibabacloud SDK's standard env var names) plus TINGWU_APP_KEY (an
    application created once in the Tingwu console — see scripts/README.md).
    Missing config or any failure (create/poll/timeout/download/parse) logs
    and returns None; fetch_transcript falls back to Whisper/NotebookLM.
    """
    access_key_id = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_ID")
    access_key_secret = os.environ.get("ALIBABA_CLOUD_ACCESS_KEY_SECRET")
    app_key = os.environ.get("TINGWU_APP_KEY")
    if not access_key_id or not access_key_secret or not app_key:
        logger.info("podcast: Tingwu credentials not configured, skipping for %s", video_id)
        return None

    try:
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_tingwu20230930 import models as tingwu_models
        from alibabacloud_tingwu20230930.client import Client as TingwuClient
    except ImportError:
        logger.info("podcast: alibabacloud_tingwu20230930 not installed, skipping Tingwu for %s", video_id)
        return None

    try:
        client = TingwuClient(open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
            endpoint=_TINGWU_ENDPOINT,
            region_id=_TINGWU_REGION,
        ))
        create_response = client.create_task(tingwu_models.CreateTaskRequest(
            app_key=app_key,
            type="offline",
            input=tingwu_models.CreateTaskRequestInput(
                file_url=audio_url,
                source_language="cn",
            ),
        ))
        task_id = create_response.body.data.task_id if create_response.body and create_response.body.data else None
        if not task_id:
            logger.warning("podcast: Tingwu CreateTask returned no task_id for %s", video_id)
            return None

        result_url = None
        elapsed = 0
        while elapsed < _TINGWU_POLL_TIMEOUT_SECONDS:
            time.sleep(_TINGWU_POLL_INTERVAL_SECONDS)
            elapsed += _TINGWU_POLL_INTERVAL_SECONDS
            info = client.get_task_info(task_id)
            data = info.body.data if info.body else None
            status = data.task_status if data else None
            if status == "COMPLETED":
                result_url = data.result.transcription if data.result else None
                break
            if status == "FAILED":
                logger.warning(
                    "podcast: Tingwu task failed for %s: %s",
                    video_id, data.error_message if data else "unknown error",
                )
                return None
        else:
            logger.warning(
                "podcast: Tingwu task timed out after %ds for %s",
                _TINGWU_POLL_TIMEOUT_SECONDS, video_id,
            )
            return None

        if not result_url:
            logger.warning("podcast: Tingwu task completed with no transcription result for %s", video_id)
            return None

        transcript = _parse_tingwu_transcript(json.loads(_http_get(result_url, timeout=30)))
    except Exception as e:
        logger.warning("podcast: Tingwu transcription failed for %s: %s", video_id, e)
        return None

    if transcript:
        logger.info("podcast: Tingwu transcribed %s (%d chars)", video_id, len(transcript))
    return transcript or None


def _resolve_transcriber(cfg: dict) -> str:
    """Normalize podcast_config into one of auto|tingwu|whisper|notebooklm|off.

    Reads the current `transcriber` key when set to a legal value; otherwise
    falls back to the legacy `whisper_fallback` key (#485) so old installs
    keep behaving the same way without a data migration: whisper_fallback=0
    -> off, anything else -> auto (NotebookLM #486 -> Tingwu #498 -> Whisper
    #485, per fetch_transcript's ordering, reordered free-first in #510).
    """
    val = cfg.get("transcriber")
    if val in ("auto", "tingwu", "notebooklm", "whisper", "off"):
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
    0/empty disables the gate. Tingwu/NotebookLM are never subject to it."""
    raw = cfg.get("whisper_max_minutes", str(_DEFAULT_WHISPER_MAX_MINUTES))
    try:
        max_minutes = float(raw)
    except (TypeError, ValueError):
        max_minutes = _DEFAULT_WHISPER_MAX_MINUTES
    if max_minutes <= 0:
        return True
    return duration <= max_minutes * 60


def fetch_transcript(video: dict) -> tuple[str | None, dict]:
    """Get the Chinese transcript for one RSS episode. `video` needs
    video_id/title/audio_url/duration_seconds (an episode row or a
    fetch_new_videos() entry both satisfy this).

    Returns (transcript_text_or_None, meta) — meta always has at least
    'title' and 'transcript_source' (one of 'notebooklm'/'tingwu'/'whisper'/
    None). Tries the transcription chain in order — NotebookLM (#486, free
    but unofficial/optional, first per #510 since it's free) -> Tingwu (#498,
    cheap, submitted the RSS mp3 URL directly, no download) -> Whisper (#485,
    paid, gated to duration <= whisper_max_minutes) — per
    podcast_config.transcriber ('auto' tries all three in that order; a
    specific value tries only that one). transcript is None when the chain
    is disabled/unavailable/fails entirely for this episode (caller stores
    status='no_transcript' in that case).

    Each step is wrapped in its own try/except (#510): an exception raised
    by one transcriber (e.g. a 429 from OpenAI mid-Whisper-call) must not
    abort the whole chain and skip the remaining steps — that bug is exactly
    what stranded short episodes with no transcript at all on 2026-07-12,
    since the (paid, duration-gated) Whisper step used to run inside the
    same try/except as the NotebookLM step that would otherwise have caught
    them.

    NotebookLM and Whisper both need the audio downloaded+transcoded first;
    Tingwu doesn't (it's submitted the RSS URL directly). The download is
    attempted at most once and its result reused by whichever of
    NotebookLM/Whisper actually runs — a download failure only rules out
    those two, Tingwu can still be tried.
    """
    video_id = video["video_id"]
    title = video.get("title") or video_id
    audio_url = video.get("audio_url")
    duration = video.get("duration_seconds") or 0
    meta = {"title": title, "transcript_source": None}

    if not audio_url:
        logger.warning("podcast: no audio_url for %s, cannot transcribe", video_id)
        return None, meta

    if duration and duration > _AUDIO_MAX_SECONDS:
        logger.warning(
            "podcast: %s is %.1fh, exceeds the %.0fh audio transcription cost guardrail, skipping",
            video_id, duration / 3600, _AUDIO_MAX_SECONDS / 3600,
        )
        return None, meta

    cfg = database.get_podcast_config()
    transcriber = _resolve_transcriber(cfg)
    if transcriber == "off":
        logger.info("podcast: transcriber=off, skipping transcription for %s", video_id)
        return None, meta

    from routes.utils import DISABLE_AI
    if DISABLE_AI:
        # Dev mode must never trigger transcription (Tingwu/Whisper cost
        # money; NotebookLM is free but still an external side effect).
        logger.info("podcast: DISABLE_AI set, skipping transcription for %s", video_id)
        return None, meta

    with tempfile.TemporaryDirectory() as tmp_dir:
        download_state = {"attempted": False, "path": None}

        def get_mp3_path() -> str | None:
            """Lazily download+transcode the audio once, reused by both the
            NotebookLM and Whisper steps below. Returns None (logged) on
            missing ffmpeg or a download/transcode failure — callers treat
            that the same as "this step can't run", not a chain-abort."""
            if download_state["attempted"]:
                return download_state["path"]
            download_state["attempted"] = True
            if not shutil.which("ffmpeg"):
                logger.warning("podcast: ffmpeg not found, skipping audio download for %s", video_id)
                return None
            try:
                download_state["path"] = _download_audio(audio_url, video_id, tmp_dir)
            except Exception as e:
                logger.warning("podcast: audio download failed for %s: %s", video_id, e)
            return download_state["path"]

        # 1. NotebookLM (#486, free, tried first per #510) — only attempted
        # when credentials are present, so 'auto' doesn't waste a download
        # on it when it can't possibly work.
        if transcriber in ("auto", "notebooklm"):
            if _notebooklm_credentials_available():
                transcript = None
                try:
                    mp3_path = get_mp3_path()
                    if mp3_path:
                        transcript = _transcribe_via_notebooklm(mp3_path, video_id)
                except Exception as e:
                    logger.warning("podcast: NotebookLM step raised for %s: %s", video_id, e)
                if transcript:
                    meta["transcript_source"] = "notebooklm"
                    logger.info("podcast: transcript source for %s = notebooklm", video_id)
                    return transcript, meta
            if transcriber == "notebooklm":
                return None, meta

        # 2. Tingwu (#498, cheap, no download needed — can still run even if
        # the download above failed/was skipped).
        if transcriber in ("auto", "tingwu"):
            transcript = None
            try:
                transcript = _transcribe_via_tingwu(audio_url, video_id)
            except Exception as e:
                logger.warning("podcast: Tingwu step raised for %s: %s", video_id, e)
            if transcript:
                meta["transcript_source"] = "tingwu"
                logger.info("podcast: transcript source for %s = tingwu", video_id)
                return transcript, meta
            if transcriber == "tingwu":
                logger.info("podcast: transcriber=tingwu and Tingwu failed, not trying further for %s", video_id)
                return None, meta

        # 3. Whisper (#485, paid, last resort — gated to short episodes).
        if transcriber in ("auto", "whisper"):
            if _whisper_duration_allowed(duration, cfg):
                transcript = None
                try:
                    mp3_path = get_mp3_path()
                    if mp3_path:
                        transcript = _transcribe_via_whisper(mp3_path, duration, video_id, tmp_dir)
                except Exception as e:
                    logger.warning("podcast: Whisper step raised for %s: %s", video_id, e)
                if transcript:
                    meta["transcript_source"] = "whisper"
                    logger.info("podcast: transcript source for %s = whisper", video_id)
                    return transcript, meta
            else:
                logger.info(
                    "podcast: %s is %.0fmin, over whisper_max_minutes — skipping Whisper",
                    video_id, duration / 60,
                )
            if transcriber == "whisper":
                return None, meta

    logger.info("podcast: transcript source for %s = none (all paths exhausted)", video_id)
    return None, meta


# ---------------------------------------------------------------------------
# AI summary
# ---------------------------------------------------------------------------

def summarize(transcript: str, title: str, detail_level: str) -> dict:
    """Summarize a transcript into {"summary_de", "words"} (#479, NotebookLM
    path added in #510). When podcast_config.summarizer is 'auto' (default)
    and NotebookLM credentials are present, tries the free
    _summarize_via_notebooklm path first; any failure (or summarizer='api')
    falls back to the paid/quota-limited API chain in
    ai.summarize_podcast_transcript so the pipeline never breaks over it."""
    cfg = database.get_podcast_config()
    summarizer = cfg.get("summarizer") or "auto"
    if summarizer == "auto" and _notebooklm_credentials_available():
        result = _summarize_via_notebooklm(transcript, title, detail_level)
        if result:
            return result
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
        <a href="{episode['youtube_url']}">Folge</a>
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


def send_signal(episode: dict) -> bool:
    """Send a plain-text Signal "Note to Self" notification for a freshly-
    summarized episode via a linked-device signal-cli install (#521).
    Returns True if sent, False if skipped (SIGNAL_ACCOUNT not configured)
    — skipping is not an error, mirrors send_email. Never raises."""
    account = os.environ.get("SIGNAL_ACCOUNT")
    if not account:
        logger.info("podcast: SIGNAL_ACCOUNT not configured, skipping Signal notification for %s",
                     episode["video_id"])
        return False

    cli_path = os.environ.get("SIGNAL_CLI_PATH", "signal-cli")
    public_base = os.environ.get("PUBLIC_BASE_URL", "https://powerdaniel3000.duckdns.org")
    transcript_link = f"{public_base}/#podcast-{episode['id']}"

    # summary_de may be None; strip HTML tags (the email version is HTML).
    # Send the FULL summary (#541) — Daniel reads it directly in Signal and the
    # old 1500-char cap cut it off mid-sentence. Keep only a high safety cap so
    # a pathologically long summary can't produce a runaway message; a normal
    # "detailed" summary (~900-1300 words ≈ up to ~9000 chars) fits well under it.
    summary_de = re.sub(r"<[^>]+>", "", episode.get("summary_de") or "").strip()
    if len(summary_de) > 12000:
        summary_de = summary_de[:12000].rstrip() + "…"

    # hsk_words comes back as a list from database.get_episode (_hydrate
    # parses the stored JSON), but be defensive in case a raw row or a
    # pre-hydration dict is ever passed in.
    hsk_words = episode.get("hsk_words") or []
    if isinstance(hsk_words, str):
        try:
            hsk_words = json.loads(hsk_words) if hsk_words else []
        except (ValueError, TypeError):
            hsk_words = []

    word_lines = "\n".join(
        f"- {w.get('word', '')} ({w.get('pinyin', '')}) – {w.get('definition_de', '')}"
        for w in hsk_words[:10]
    )

    # 抬头行：播客名 · 星期几（德语） · 日期（#532）。播客名从 channel_id
    # （feed 的 url）反查 podcast_feeds；查不到就省略播客名部分，只留星期+日期。
    feed_title = None
    channel_id = episode.get("channel_id")
    if channel_id:
        feed = database.get_feed_by_url(channel_id)
        if feed:
            feed_title = feed.get("title")

    weekday_de = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    date_part = None
    raw_date = episode.get("published_at") or episode.get("created_at")
    if raw_date:
        try:
            dt = datetime.fromisoformat(raw_date).astimezone(ZoneInfo("Europe/Berlin"))
            date_part = f"{weekday_de[dt.weekday()]} · {dt.strftime('%d.%m.%Y')}"
        except (ValueError, TypeError):
            date_part = None

    header_parts = [p for p in (feed_title, date_part) if p]
    lines = [" · ".join(header_parts)] if header_parts else []
    lines.append(f"🎙 {episode['title']}")
    if summary_de:
        lines.append("")
        lines.append(summary_de)
    if word_lines:
        lines.append("")
        lines.append("Neue HSK5+ Vokabeln:")
        lines.append(word_lines)
    lines.append("")
    lines.append(f"🔗 {transcript_link}")
    if episode.get("spotify_url"):
        lines.append(episode["spotify_url"])
    text = "\n".join(lines)

    try:
        result = subprocess.run(
            [cli_path, "-a", account, "send", "--note-to-self", "-m", text],
            capture_output=True, timeout=60,
        )
    except Exception as e:
        logger.warning("podcast: signal-cli invocation failed for %s: %s", episode["video_id"], e)
        return False

    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", "replace") if isinstance(result.stderr, bytes) else result.stderr
        logger.warning("podcast: signal-cli exited %s for %s: %s",
                        result.returncode, episode["video_id"], stderr)
        return False

    logger.info("podcast: Signal notification sent for %s", episode["video_id"])
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
    run_check (new episodes + the auto-retry pass) and the manual retry
    endpoint (#491). `video` needs 'video_id', 'title', 'audio_url' and
    'duration_seconds' (#497 — fetch_transcript needs the last two now that
    there's no yt-dlp metadata lookup to fall back on).

    Never raises — any unexpected failure marks the episode 'error' and bumps
    summary['failed'] (a missing transcript is 'no_transcript', not a
    failure); success bumps summary['summarized'] / summary['emailed'].
    """
    from routes.utils import DISABLE_AI

    label = f"Transcribe & Summarize: {video['title'][:30]}"
    with database.action_context(label):
        try:
            # Reuse an already-stored transcript (#500): after e.g. an OpenAI-quota
            # failure in the summary step, the retry must not re-run the whole
            # transcription (a NotebookLM upload+indexing round takes ~10 minutes
            # and Tingwu/Whisper cost money) — the transcript is already good.
            existing = database.get_episode(episode_id) or {}
            stored = (existing.get("transcript_zh") or "").strip()
            if stored:
                transcript = _normalize_transcript(stored)
                meta = {"transcript_source": existing.get("transcript_source")}
                logger.info("podcast: reusing existing transcript for %s (%d chars)",
                            video["video_id"], len(transcript))
                if transcript != stored:
                    database.update_episode(episode_id, transcript_zh=transcript)
            else:
                transcript, meta = fetch_transcript(video)
                transcript = _normalize_transcript(transcript) if transcript else transcript
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

            try:
                send_signal(episode)
            except Exception as e:
                # Signal and email are independent, best-effort channels — a
                # signal-cli hiccup must not downgrade a successfully
                # summarized episode to 'error' either.
                logger.warning("podcast: Signal notification failed for %s: %s", video["video_id"], e)
        except Exception as e:
            logger.error("podcast: episode %s failed: %s", video["video_id"], e)
            database.update_episode(episode_id, status="error", error=str(e))
            summary["failed"] += 1


def retry_episode(episode_id: int) -> dict:
    """Re-run the full processing pipeline for one existing episode (#491) —
    used by POST /api/podcast/episodes/{id}/retry after e.g. a failed
    transcription attempt. Reuses the existing row (video_id is UNIQUE — no
    second INSERT) after resetting its status/error, so a retry that fails
    again just lands back on 'error' with the fresh message.

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
    video = {
        "video_id": episode["video_id"], "title": episode["title"],
        "audio_url": episode.get("audio_url"), "duration_seconds": episode.get("duration_seconds"),
    }
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
            video.get("audio_url"), video.get("duration_seconds"),
        )
        summary["new"] += 1
        # Auto-processing (#502): only immediately transcribe+summarize when
        # the source feed has auto_process=1 *and* this isn't part of a
        # feed's first-run backfill — a freshly-subscribed feed's back
        # catalog is stored metadata-only, transcribed on demand from the UI.
        if video.get("auto_process") and not video.get("is_backfill"):
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
        _process_episode(ep["id"], {
            "video_id": ep["video_id"], "title": ep["title"],
            "audio_url": ep.get("audio_url"), "duration_seconds": ep.get("duration_seconds"),
        }, detail_level, summary)

    return summary
