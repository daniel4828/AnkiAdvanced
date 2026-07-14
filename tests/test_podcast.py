"""
Tests for podcast.py — Tingwu transcript parsing with paragraph timestamps (#543).

Fast tests, no credentials/network: feed _parse_tingwu_transcript synthetic
Tingwu-shaped JSON and assert the flattened text carries [MM:SS] prefixes.
"""

import podcast


def test_paragraph_level_start_becomes_timestamp():
    """Each paragraph is prefixed with its start time; past the hour the
    format grows to [H:MM:SS]."""
    result = {"Transcription": {"Paragraphs": [
        {"Text": "大家好欢迎收听", "Start": 0},
        {"Text": "今天聊一聊房价", "Start": 754000},   # 12:34
        {"Text": "最后总结", "Start": 3661000},        # 1:01:01
    ]}}
    assert podcast._parse_tingwu_transcript(result) == (
        "[00:00] 大家好欢迎收听 [12:34] 今天聊一聊房价 [1:01:01] 最后总结"
    )


def test_words_start_used_when_paragraph_has_no_text():
    """A paragraph given only as Words[] is joined, and its timestamp falls
    back to the first word's start."""
    result = {"Paragraphs": [
        {"Words": [{"Text": "再", "Start": 5000}, {"Text": "见", "Start": 5400}]},
    ]}
    assert podcast._parse_tingwu_transcript(result) == "[00:05] 再见"


def test_paragraph_without_timing_stays_plain():
    """No start field anywhere -> the paragraph is emitted without a prefix
    (never a broken '[..]')."""
    result = {"Paragraphs": [{"Text": "无时间戳的一段"}]}
    assert podcast._parse_tingwu_transcript(result) == "无时间戳的一段"


def test_unknown_shape_falls_back_to_recursive_text_collection():
    """An undocumented shape with no Paragraphs still degrades to the
    concatenated Text strings (the pre-#543 fallback, unchanged)."""
    result = {"Weird": {"Nested": [{"Text": "abc"}, {"Text": "def"}]}}
    assert podcast._parse_tingwu_transcript(result) == "abc def"


def test_fmt_timestamp_boundaries():
    assert podcast._fmt_timestamp(0) == "[00:00]"
    assert podcast._fmt_timestamp(59999) == "[00:59]"
    assert podcast._fmt_timestamp(60000) == "[01:00]"
    assert podcast._fmt_timestamp(3600000) == "[1:00:00]"
