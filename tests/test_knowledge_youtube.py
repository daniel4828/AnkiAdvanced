"""Tests for knowledge/youtube.py (issue #651).

parse_video_id is pure (no I/O). fetch_metadata and fetch_captions both make
real network/API calls in production, so every test here stubs them out —
CLAUDE.md is explicit that this suite must never actually reach YouTube.
"""
import pytest

import knowledge.youtube as ky
from youtube_transcript_api._errors import (
    CouldNotRetrieveTranscript,
    RequestBlocked,
    TranscriptsDisabled,
)


# ---------------------------------------------------------------------------
# parse_video_id
# ---------------------------------------------------------------------------

def test_parse_watch_url():
    assert ky.parse_video_id("https://www.youtube.com/watch?v=abc123XYZ_-") == "abc123XYZ_-"


def test_parse_watch_url_with_extra_query_params():
    assert ky.parse_video_id(
        "https://www.youtube.com/watch?v=abc123XYZ_-&t=42s&list=PLxyz"
    ) == "abc123XYZ_-"


def test_parse_youtu_be_short_link():
    assert ky.parse_video_id("https://youtu.be/abc123XYZ_-") == "abc123XYZ_-"


def test_parse_youtu_be_with_query_params():
    assert ky.parse_video_id("https://youtu.be/abc123XYZ_-?t=5") == "abc123XYZ_-"


def test_parse_shorts_url():
    assert ky.parse_video_id("https://www.youtube.com/shorts/abc123XYZ_-") == "abc123XYZ_-"


def test_parse_mobile_watch_url():
    assert ky.parse_video_id("https://m.youtube.com/watch?v=abc123XYZ_-") == "abc123XYZ_-"


def test_parse_no_www_prefix():
    assert ky.parse_video_id("https://youtube.com/watch?v=abc123XYZ_-") == "abc123XYZ_-"


def test_parse_rejects_non_youtube_url():
    assert ky.parse_video_id("https://example.com/watch?v=abc123XYZ_-") is None


def test_parse_rejects_youtube_homepage():
    assert ky.parse_video_id("https://www.youtube.com/") is None


def test_parse_rejects_empty_and_none():
    assert ky.parse_video_id("") is None
    assert ky.parse_video_id(None) is None


def test_parse_rejects_garbage_string():
    assert ky.parse_video_id("not a url at all") is None


def test_parse_watch_url_missing_v_param():
    assert ky.parse_video_id("https://www.youtube.com/watch?list=PLxyz") is None


# ---------------------------------------------------------------------------
# fetch_metadata (oEmbed) — HTTP stubbed
# ---------------------------------------------------------------------------

def test_fetch_metadata_returns_title_and_author(monkeypatch):
    def fake_get_json(url, timeout=10):
        assert "oembed" in url
        assert "abc123" in url
        return {"title": "一个视频标题", "author_name": "某频道"}

    monkeypatch.setattr(ky, "_http_get_json", fake_get_json)
    meta = ky.fetch_metadata("abc123")
    assert meta == {"title": "一个视频标题", "author_name": "某频道"}


def test_fetch_metadata_falls_back_to_video_id_when_title_missing(monkeypatch):
    monkeypatch.setattr(ky, "_http_get_json", lambda url, timeout=10: {})
    meta = ky.fetch_metadata("xyz789")
    assert meta["title"] == "xyz789"
    assert meta["author_name"] is None


# ---------------------------------------------------------------------------
# fetch_captions — youtube_transcript_api stubbed
# ---------------------------------------------------------------------------

class _FakeSnippet:
    def __init__(self, text):
        self.text = text


class _FakeTranscript:
    def __init__(self, language_code, texts, fetch_raises=None):
        self.language_code = language_code
        self._texts = texts
        self._fetch_raises = fetch_raises

    def fetch(self):
        if self._fetch_raises:
            raise self._fetch_raises
        return [_FakeSnippet(t) for t in self._texts]


class _FakeTranscriptList:
    def __init__(self, transcripts):
        self._transcripts = transcripts

    def find_transcript(self, language_codes):
        for code in language_codes:
            for t in self._transcripts:
                if t.language_code == code:
                    return t
        raise CouldNotRetrieveTranscript("fake-video-id")

    def __iter__(self):
        return iter(self._transcripts)


class _FakeApi:
    def __init__(self, transcript_list=None, list_raises=None):
        self._transcript_list = transcript_list
        self._list_raises = list_raises

    def list(self, video_id):
        if self._list_raises:
            raise self._list_raises
        return self._transcript_list


def _patch_api(monkeypatch, fake_api):
    import youtube_transcript_api
    monkeypatch.setattr(youtube_transcript_api, "YouTubeTranscriptApi", lambda: fake_api)


def test_fetch_captions_picks_priority_language(monkeypatch):
    transcripts = _FakeTranscriptList([
        _FakeTranscript("en", ["hello", "world"]),
        _FakeTranscript("zh-Hans", ["你好", "世界"]),
    ])
    _patch_api(monkeypatch, _FakeApi(transcript_list=transcripts))

    text, meta = ky.fetch_captions("vid1")
    assert text == "你好世界"  # CJK-adjacent whitespace collapsed by _normalize_transcript
    assert meta["transcript_source"] == "youtube_captions"
    assert meta["language_code"] == "zh-Hans"


def test_fetch_captions_falls_back_to_any_available_language(monkeypatch):
    """None of the priority languages (zh*/de/en) exist — falls back to
    whatever single track is there (e.g. French)."""
    transcripts = _FakeTranscriptList([
        _FakeTranscript("fr", ["Bonjour", "le", "monde"]),
    ])
    _patch_api(monkeypatch, _FakeApi(transcript_list=transcripts))

    text, meta = ky.fetch_captions("vid2")
    assert text == "Bonjour le monde"
    assert meta["language_code"] == "fr"


def test_fetch_captions_no_captions_at_all_returns_none(monkeypatch):
    """list() itself fails (captions disabled/video unavailable) -> (None, meta),
    not an exception — the caller stores status='no_transcript' for this."""
    _patch_api(monkeypatch, _FakeApi(list_raises=CouldNotRetrieveTranscript("novid")))

    text, meta = ky.fetch_captions("novid")
    assert text is None
    assert meta["transcript_source"] == "youtube_captions"


def test_fetch_captions_empty_transcript_list_returns_none(monkeypatch):
    """A transcript list that has zero tracks (find_transcript AND the
    fallback iteration both come up empty) is treated the same as no captions."""
    _patch_api(monkeypatch, _FakeApi(transcript_list=_FakeTranscriptList([])))

    text, meta = ky.fetch_captions("vid3")
    assert text is None


def test_fetch_captions_fetch_failure_returns_none(monkeypatch):
    """The track exists but fetch() itself fails (e.g. transient API error) —
    treated as no-transcript rather than propagating, matching podcast.py's
    pattern of a soft failure for 'this source can't be transcribed'."""
    transcripts = _FakeTranscriptList([
        _FakeTranscript("en", [], fetch_raises=CouldNotRetrieveTranscript("vid4")),
    ])
    _patch_api(monkeypatch, _FakeApi(transcript_list=transcripts))

    text, meta = ky.fetch_captions("vid4")
    assert text is None


# ---------------------------------------------------------------------------
# fetch_captions — YouTube refusing the request (#681)
# ---------------------------------------------------------------------------

def _patch_notebooklm(monkeypatch, result):
    """Stub podcast.transcribe_url_via_notebooklm; returns the list of
    (url, video_id) calls it received so tests can assert it actually ran."""
    import podcast
    calls = []

    def fake(url, video_id):
        calls.append((url, video_id))
        return result

    monkeypatch.setattr(podcast, "transcribe_url_via_notebooklm", fake)
    return calls


def test_blocked_request_falls_back_to_notebooklm(monkeypatch):
    """RequestBlocked (the production server's cloud IP being banned) must NOT
    be reported as 'no captions' — it goes to NotebookLM instead."""
    _patch_api(monkeypatch, _FakeApi(list_raises=RequestBlocked("vid6")))
    calls = _patch_notebooklm(monkeypatch, "大家好 欢迎收听")

    text, meta = ky.fetch_captions("vid6")
    assert text == "大家好欢迎收听"  # normalized like every other transcript
    assert meta["transcript_source"] == "notebooklm"
    assert calls == [("https://www.youtube.com/watch?v=vid6", "vid6")]


def test_blocked_during_fetch_also_falls_back(monkeypatch):
    """The block can hit on the track fetch rather than the listing."""
    transcripts = _FakeTranscriptList([
        _FakeTranscript("zh-Hans", [], fetch_raises=RequestBlocked("vid7")),
    ])
    _patch_api(monkeypatch, _FakeApi(transcript_list=transcripts))
    calls = _patch_notebooklm(monkeypatch, "内容")

    text, meta = ky.fetch_captions("vid7")
    assert text == "内容"
    assert meta["transcript_source"] == "notebooklm"
    assert len(calls) == 1


def test_blocked_with_failing_fallback_raises(monkeypatch):
    """Both sources failed: this is an error the episode must surface
    (status='error'), never a silent 'no_transcript'."""
    _patch_api(monkeypatch, _FakeApi(list_raises=RequestBlocked("vid8")))
    _patch_notebooklm(monkeypatch, None)

    with pytest.raises(ky.CaptionsUnavailable) as excinfo:
        ky.fetch_captions("vid8")
    assert "RequestBlocked" in str(excinfo.value)


def test_blocked_with_blank_fallback_raises(monkeypatch):
    """A whitespace-only NotebookLM result is no transcript at all."""
    _patch_api(monkeypatch, _FakeApi(list_raises=RequestBlocked("vid9")))
    _patch_notebooklm(monkeypatch, "   \n ")

    with pytest.raises(ky.CaptionsUnavailable):
        ky.fetch_captions("vid9")


def test_missing_captions_never_calls_notebooklm(monkeypatch):
    """A video that truly has no caption track stays a cheap 'no_transcript' —
    it must not spend a ~minutes-long NotebookLM round on the way there."""
    _patch_api(monkeypatch, _FakeApi(list_raises=TranscriptsDisabled("vid10")))
    calls = _patch_notebooklm(monkeypatch, "should not be used")

    text, _ = ky.fetch_captions("vid10")
    assert text is None
    assert calls == []


def test_blocked_error_types_are_transcript_error_subclasses():
    """Guards the exact bug: these all subclass CouldNotRetrieveTranscript, so
    catching the base class first swallows them as 'no captions'. If a future
    library version renames one, _blocked_error_types() drops it silently —
    this asserts the ones we rely on are still resolvable."""
    types = ky._blocked_error_types()
    assert RequestBlocked in types
    assert all(issubclass(t, CouldNotRetrieveTranscript) for t in types)


def test_fetch_captions_joins_and_normalizes_chinese_text(monkeypatch):
    """Regression check for the actual bug class _normalize_transcript exists
    for: naive space-joining of CJK snippets must not leave stray spaces."""
    transcripts = _FakeTranscriptList([
        _FakeTranscript("zh-CN", ["大家好", "欢迎收听", "今天的节目"]),
    ])
    _patch_api(monkeypatch, _FakeApi(transcript_list=transcripts))

    text, meta = ky.fetch_captions("vid5")
    assert text == "大家好欢迎收听今天的节目"
    assert " " not in text
