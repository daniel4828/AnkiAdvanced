"""Tests for knowledge/signal_inbox.py (issue #749).

signal-cli is entirely faked: `runner` (injected into check_signal_inbox)
is a plain callable that returns canned JSON-Lines stdout instead of
actually shelling out — CLAUDE.md is explicit that test suites must never
reach real network services / external processes, mirroring how
knowledge/mailbox.py fakes IMAP via `imap_factory`.

knowledge.ingest.ingest_url and podcast.retry_episode/send_signal_text are
monkeypatched too: exercising the real ingest/transcribe/summarize pipeline
is covered elsewhere (tests/test_knowledge_youtube.py,
tests/test_knowledge_article.py, tests/test_podcast*.py).
"""
import json

import pytest

import database
import knowledge.ingest
import knowledge.signal_inbox as signal_inbox
import podcast


ACCOUNT = "+491234567890"
OTHER = "+499999999999"


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Fresh temp database — retry-queue state lives in app_settings.
    Patch database.core.DB_PATH, not database.DB_PATH (issue #615: the
    latter is only a name copy, get_db() reads the former)."""
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


@pytest.fixture(autouse=True)
def _no_real_signal(monkeypatch):
    """Belt-and-suspenders: even if a test forgets to patch send_signal_text
    itself, nothing here should ever shell out to a real signal-cli."""
    monkeypatch.setattr(podcast, "send_signal_text", lambda text, context="signal": True)


def _envelope_note_to_self(message: str, source=ACCOUNT, destination=ACCOUNT) -> dict:
    """A linked-device sync copy of a message the phone sent to Note to
    Self: source == account, sentMessage.destination == account."""
    return {
        "sourceNumber": source,
        "syncMessage": {"sentMessage": {"destinationNumber": destination, "message": message}},
    }


def _envelope_sent_to_other(message: str) -> dict:
    """A sync copy of a message Daniel's phone sent to someone ELSE — same
    source as Note to Self, different destination. Must be ignored."""
    return {
        "sourceNumber": ACCOUNT,
        "syncMessage": {"sentMessage": {"destinationNumber": "+493333333333", "message": message}},
    }


def _envelope_sent_to_group(message: str) -> dict:
    """A sync copy of a message Daniel's phone sent to a GROUP — same
    source as Note to Self, no destinationNumber at all, only groupInfo.
    This is the fail-closed case: a naive "no destination -> allow" check
    would wave this through. Must be ignored."""
    return {
        "sourceNumber": ACCOUNT,
        "syncMessage": {"sentMessage": {"groupInfo": {"groupId": "abc123"}, "message": message}},
    }


def _envelope_sent_message_no_destination(message: str) -> dict:
    """A sentMessage with no destination and no groupInfo either — an
    ambiguous/unrecognized shape. Must fail closed (ignored), not guessed
    at as Note to Self."""
    return {
        "sourceNumber": ACCOUNT,
        "syncMessage": {"sentMessage": {"message": message}},
    }


def _envelope_from_other(message: str) -> dict:
    """An ordinary incoming message from someone who is not Daniel's own
    account. Must be ignored — the security gate under test."""
    return {
        "sourceNumber": OTHER,
        "dataMessage": {"message": message},
    }


def _lines(*envelopes) -> str:
    return "\n".join(json.dumps(e) for e in envelopes)


def _make_runner(stdout: str, calls=None):
    def runner(args):
        if calls is not None:
            calls.append(args)
        return stdout
    return runner


# ---------------------------------------------------------------------------
# SIGNAL_ACCOUNT not configured
# ---------------------------------------------------------------------------

def test_no_account_configured_skips_everything(tmp_db, monkeypatch):
    monkeypatch.delenv("SIGNAL_ACCOUNT", raising=False)
    calls = []
    summary = signal_inbox.check_signal_inbox(runner=_make_runner("", calls))

    assert summary["reason"] == "no_account"
    assert calls == []


# ---------------------------------------------------------------------------
# Security: only Note-to-Self from the account itself is ingested
# ---------------------------------------------------------------------------

def test_message_from_someone_else_is_skipped(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_from_other("https://example.com/from-stranger"))

    ingest_calls = []
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: ingest_calls.append(url) or {"episode_id": 1})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["skipped"] == 1
    assert summary["checked"] == 1
    assert ingest_calls == []


def test_message_sent_to_someone_else_is_skipped(tmp_db, monkeypatch):
    """Same source (Daniel's own account) as Note to Self, but addressed to
    a different destination — must not be treated as inbox input."""
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_sent_to_other("https://example.com/to-a-friend"))

    ingest_calls = []
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: ingest_calls.append(url) or {"episode_id": 1})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["skipped"] == 1
    assert ingest_calls == []


def test_message_sent_to_a_group_is_skipped(tmp_db, monkeypatch):
    """Same source as Note to Self, but sent to a GROUP (no destination
    field, only groupInfo) — the fail-closed bug this test guards against:
    a naive "no destination -> allow" check would ingest every link Daniel
    ever shares with a group chat, without him knowing."""
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_sent_to_group("https://example.com/group-link"))

    ingest_calls = []
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: ingest_calls.append(url) or {"episode_id": 1})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["skipped"] == 1
    assert ingest_calls == []


def test_sent_message_with_no_destination_is_skipped(tmp_db, monkeypatch):
    """No destination and no groupInfo either — an unrecognized/ambiguous
    shape must fail closed, not be guessed at as Note to Self."""
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_sent_message_no_destination("https://example.com/ambiguous"))

    ingest_calls = []
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: ingest_calls.append(url) or {"episode_id": 1})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["skipped"] == 1
    assert ingest_calls == []


# ---------------------------------------------------------------------------
# Note-to-Self URLs are extracted and ingested
# ---------------------------------------------------------------------------

def test_note_to_self_url_is_ingested(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("Schau dir das an: https://example.com/article-1"))

    ingest_calls = []

    def fake_ingest(url):
        ingest_calls.append(url)
        return {"episode_id": 1}

    monkeypatch.setattr(knowledge.ingest, "ingest_url", fake_ingest)
    # New episode -> processing is triggered too; keep it a no-op here.
    monkeypatch.setattr(podcast, "retry_episode", lambda episode_id: {"status": "summarized", "error": None})
    monkeypatch.setattr(database, "get_episode", lambda episode_id: {"id": episode_id, "title": "Article One"})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert ingest_calls == ["https://example.com/article-1"]
    assert summary["ingested"] == 1
    assert summary["processed"] == 1
    assert summary["results"][0]["ok"] is True
    assert summary["results"][0]["episode_id"] == 1


def test_dedup_same_url_twice_ingests_once(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(
        _envelope_note_to_self("https://example.com/dup"),
        _envelope_note_to_self("nochmal: https://example.com/dup"),
    )

    ingest_calls = []
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: ingest_calls.append(url) or {"episode_id": 1})
    monkeypatch.setattr(podcast, "retry_episode", lambda episode_id: {"status": "summarized", "error": None})
    monkeypatch.setattr(database, "get_episode", lambda episode_id: {"id": episode_id, "title": "Dup"})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert ingest_calls == ["https://example.com/dup"]
    assert summary["ingested"] == 1


def test_already_existing_episode_is_not_reprocessed(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("https://example.com/already-there"))

    monkeypatch.setattr(knowledge.ingest, "ingest_url",
                         lambda url: {"status": "already_exists", "episode_id": 7})
    process_calls = []
    monkeypatch.setattr(podcast, "retry_episode", lambda episode_id: process_calls.append(episode_id) or {"status": "summarized"})
    monkeypatch.setattr(database, "get_episode", lambda episode_id: {"id": episode_id, "title": "Already There"})

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert process_calls == []
    assert summary["results"][0]["ok"] is True


# ---------------------------------------------------------------------------
# Retry queue for failed ingests
# ---------------------------------------------------------------------------

def test_ingest_failure_goes_to_retry_queue(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("https://example.com/flaky"))

    def failing_ingest(url):
        raise knowledge.ingest.IngestError("temporary failure")

    monkeypatch.setattr(knowledge.ingest, "ingest_url", failing_ingest)

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["failed"] == 0  # not yet given up
    assert len(summary["errors"]) == 1
    queue = signal_inbox._load_retry_queue()
    assert queue == [{"url": "https://example.com/flaky", "attempts": 1}]


def test_first_failure_still_sends_a_receipt_line(tmp_db, monkeypatch):
    """Bug fix: the "📥 已收到…开始处理" notice was being sent even when the
    only outcome was "queued for retry", leaving Daniel with no follow-up
    message at all — no way to tell "still running" from "silently
    retrying" from "died". Every URL that triggers the start notice must
    produce a corresponding line in the final receipt, even on attempt 1."""
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("https://example.com/flaky"))

    def failing_ingest(url):
        raise knowledge.ingest.IngestError("temporary failure")

    monkeypatch.setattr(knowledge.ingest, "ingest_url", failing_ingest)

    sent = []
    monkeypatch.setattr(podcast, "send_signal_text", lambda text, context="signal": sent.append(text) or True)

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["failed"] == 0
    # sent[0] is the "received, starting" notice; sent[1] is the receipt —
    # the receipt must mention the URL even though it isn't a final failure.
    assert len(sent) == 2
    assert "https://example.com/flaky" in sent[1]


def test_retry_queue_is_retried_and_dropped_after_three_attempts(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)

    def failing_ingest(url):
        raise knowledge.ingest.IngestError("still broken")

    monkeypatch.setattr(knowledge.ingest, "ingest_url", failing_ingest)

    # No new messages this run — only the retry queue is processed.
    signal_inbox._save_retry_queue([{"url": "https://example.com/flaky", "attempts": 2}])

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(""))

    assert summary["failed"] == 1
    assert signal_inbox._load_retry_queue() == []
    assert any("已放弃" in line or "flaky" in line for line in summary["errors"])


def test_retry_queue_processed_before_new_messages(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    signal_inbox._save_retry_queue([{"url": "https://example.com/old", "attempts": 1}])
    stdout = _lines(_envelope_note_to_self("https://example.com/new"))

    order = []

    def fake_ingest(url):
        order.append(url)
        return {"episode_id": 1}

    monkeypatch.setattr(knowledge.ingest, "ingest_url", fake_ingest)
    monkeypatch.setattr(podcast, "retry_episode", lambda episode_id: {"status": "summarized"})
    monkeypatch.setattr(database, "get_episode", lambda episode_id: {"id": episode_id, "title": "T"})

    signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert order == ["https://example.com/old", "https://example.com/new"]


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------

def test_no_receipt_sent_when_nothing_to_do(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("kein Link hier, nur Text"))

    sent = []
    monkeypatch.setattr(podcast, "send_signal_text", lambda text, context="signal": sent.append(text) or True)

    summary = signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    assert summary["skipped"] == 1
    assert sent == []


def test_receipt_sent_when_url_processed(tmp_db, monkeypatch):
    monkeypatch.setenv("SIGNAL_ACCOUNT", ACCOUNT)
    stdout = _lines(_envelope_note_to_self("https://example.com/one"))

    sent = []
    monkeypatch.setattr(podcast, "send_signal_text", lambda text, context="signal": sent.append(text) or True)
    monkeypatch.setattr(knowledge.ingest, "ingest_url", lambda url: {"episode_id": 1})
    monkeypatch.setattr(podcast, "retry_episode", lambda episode_id: {"status": "summarized"})
    monkeypatch.setattr(database, "get_episode", lambda episode_id: {"id": episode_id, "title": "One"})

    signal_inbox.check_signal_inbox(runner=_make_runner(stdout))

    # Two sends: the "received, starting" notice and the final receipt.
    assert len(sent) == 2
    assert "已收到" in sent[0]
    assert "One" in sent[1]


def test_send_receipt_noop_on_empty_lines(monkeypatch):
    called = []
    monkeypatch.setattr(podcast, "send_signal_text", lambda text, context="signal": called.append(text) or True)
    assert signal_inbox.send_receipt([]) is False
    assert called == []
