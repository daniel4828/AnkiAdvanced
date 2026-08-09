"""YouTube ingestion (issue #651): turn a video URL into a transcript the
existing podcast pipeline (podcast.summarize / _process_episode) can consume
unchanged. No audio download, no Whisper — captions only; a video with no
caption track at all falls to status='no_transcript' (see podcast.fetch_transcript).

`youtube-transcript-api` note (checked against 1.2.4, 2026-08): the library
moved from classmethods to instance methods in 1.0 — `YouTubeTranscriptApi()`
must be instantiated, then `.list(video_id)` / `.fetch(video_id, languages=)`
called on the instance. Most examples online still show the old
`YouTubeTranscriptApi.get_transcript(...)` classmethod, which no longer
exists.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# Caption language priority (issue #651): Chinese variants first (Daniel's
# primary study language), then German, then English as a last resort before
# falling back to "whatever track exists" — a German or English video still
# has *some* transcript, and the podcast summary prompt now tolerates any
# input language (ai.build_podcast_summary_prompt).
_LANGUAGE_PRIORITY = ("zh-Hans", "zh-CN", "zh", "zh-TW", "de", "en")

_OEMBED_URL = "https://www.youtube.com/oembed"


def _http_get_json(url: str, timeout: int = 10) -> dict:
    """Mirrors podcast._http_get's plain-urllib style (no extra HTTP
    dependency) but decodes JSON — used for the oEmbed metadata lookup."""
    req = urllib.request.Request(url, headers={"User-Agent": "AnkiAdvanced/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def parse_video_id(url: str) -> str | None:
    """Extract a YouTube video id from any of the URL shapes Daniel might
    paste: youtube.com/watch?v=, youtu.be/, youtube.com/shorts/,
    m.youtube.com/watch?v=, with or without extra query params. Returns None
    for anything that isn't a recognizable YouTube video URL."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None

    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "m.youtube.com":
        host = "youtube.com"

    if host in ("youtube.com", "youtube-nocookie.com"):
        if parsed.path == "/watch":
            qs = urllib.parse.parse_qs(parsed.query)
            vid = (qs.get("v") or [None])[0]
            return vid or None
        m = re.match(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]+)", parsed.path)
        if m:
            return m.group(1)
        return None

    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None

    return None


def fetch_metadata(video_id: str) -> dict:
    """Video title + channel name via oEmbed (no API key needed). Raises on
    a network/HTTP failure — the caller (routes/knowledge.py) surfaces that
    as a 400/500, since without a title there's nothing sensible to store."""
    query = urllib.parse.urlencode({
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "format": "json",
    })
    data = _http_get_json(f"{_OEMBED_URL}?{query}", timeout=10)
    return {
        "title": data.get("title") or video_id,
        "author_name": data.get("author_name"),
    }


def fetch_captions(video_id: str) -> tuple[str | None, dict]:
    """Fetch a video's caption track and join it into continuous text,
    signature-compatible with podcast.fetch_transcript's (text, meta) return.

    Tries _LANGUAGE_PRIORITY in order, then falls back to whatever single
    track exists (any language). Returns (None, meta) — not an exception —
    when the video has no captions at all (disabled, unavailable, or no
    track in any language); the caller stores status='no_transcript' for
    that, same as an un-transcribable podcast episode. This function does
    NOT fall back to downloading audio for Whisper (out of scope for #651).

    A genuine transport/API error (not "no captions") is left to propagate —
    the podcast pipeline's outer try/except stores it in episodes.error.
    """
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import CouldNotRetrieveTranscript

    meta: dict = {"transcript_source": "youtube_captions", "language_code": None}
    api = YouTubeTranscriptApi()

    try:
        transcript_list = api.list(video_id)
    except CouldNotRetrieveTranscript as e:
        logger.info("knowledge.youtube: no caption list for %s: %s", video_id, e)
        return None, meta

    transcript = None
    try:
        transcript = transcript_list.find_transcript(_LANGUAGE_PRIORITY)
    except CouldNotRetrieveTranscript:
        # None of the priority languages exist — fall back to any available
        # track rather than giving up (issue #651: "再退到任意可用语言").
        try:
            transcript = next(iter(transcript_list))
        except StopIteration:
            transcript = None

    if transcript is None:
        logger.info("knowledge.youtube: no transcript track at all for %s", video_id)
        return None, meta

    try:
        fetched = transcript.fetch()
    except CouldNotRetrieveTranscript as e:
        logger.warning("knowledge.youtube: fetch failed for %s (%s): %s",
                        video_id, transcript.language_code, e)
        return None, meta

    text = " ".join(s.text.strip() for s in fetched if (s.text or "").strip())
    if not text.strip():
        return None, meta

    # Reuse podcast's ASR cleanup (CJK-spacing collapse + Traditional ->
    # Simplified) — a local import avoids a podcast <-> knowledge import
    # cycle (podcast.fetch_transcript dispatches into this module for
    # kind='video', so this module can't import podcast at module load time).
    from podcast import _normalize_transcript
    text = _normalize_transcript(text)

    meta["language_code"] = transcript.language_code
    return text, meta
