"""
Source language → target language translation using Google Translate (via deep_translator).

The source language is configurable (defaults to Chinese, "zh-CN") so this module
can also translate other learner languages (e.g. French) into German.

Requires internet access (VPN recommended in China).
Install: pip install deep-translator
"""
import concurrent.futures
import logging

logger = logging.getLogger(__name__)

_translators: dict[tuple[str, str], object] = {}

# deep-translator's underlying requests call sets no timeout, so one stalled
# connection can hang the calling thread forever — a podcast check once froze
# for 14h this way while holding its run lock (#565).
_REQUEST_TIMEOUT_SECONDS = 90


def _translate_with_timeout(t, text: str) -> str:
    """Run t.translate(text) with a hard deadline on a throwaway thread. On
    timeout the worker thread is abandoned (it dies whenever its socket does)
    and concurrent.futures.TimeoutError propagates to the caller's existing
    fallback handling."""
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        return ex.submit(t.translate, text).result(timeout=_REQUEST_TIMEOUT_SECONDS)
    finally:
        ex.shutdown(wait=False)


def _load(source: str, target: str) -> object | None:
    key = (source, target)
    if key in _translators:
        return _translators[key]
    try:
        from deep_translator import GoogleTranslator
        t = GoogleTranslator(source=source, target=target)
        _translators[key] = t
        logger.info("translator: GoogleTranslator loaded (source=%s, target=%s)", source, target)
        return t
    except Exception as e:
        logger.error("translator: failed to load (source=%s, target=%s) — %s", source, target, e)
        _translators[key] = None
        return None


def translate_zh(text: str, target: str = "en", source: str = "zh-CN") -> str:
    """Translate a string from `source` to the target language. Returns original on failure."""
    t = _load(source, target)
    if t is None or not text.strip():
        return text
    try:
        return _translate_with_timeout(t, text) or text
    except Exception as e:
        logger.warning("translator: error (source=%s, target=%s) — %s", source, target, e)
        return text


# The free Google endpoint rejects requests beyond ~5000 characters, so a batch
# is split into chunks below that limit (podcast.py did this at its own call
# site; #756 moved it in here so every caller gets it).
_CHUNK_CHAR_BUDGET = 4500


def _translate_chunk(t, texts: list[str], target: str, source: str,
                     on_item=None) -> list[str]:
    """One HTTP request for the whole chunk; per-sentence retry on failure.
    on_item() is called once per sentence in the slow retry path only — the
    joined request has no interior progress to report."""
    sep = "\n"
    combined = sep.join(text.strip() or " " for text in texts)
    try:
        translated = _translate_with_timeout(t, combined) or combined
        parts = translated.split(sep)
        if len(parts) == len(texts):
            return [p.strip() or orig for p, orig in zip(parts, texts)]
        logger.warning("translator: split count mismatch (%d vs %d), falling back", len(parts), len(texts))
    except Exception as e:
        logger.warning("translator: batch error (source=%s, target=%s) — %s", source, target, e)

    out = []
    for text in texts:
        out.append(translate_zh(text, target, source))
        if on_item:
            on_item()
    return out


def translate_batch(texts: list[str], target: str = "en", source: str = "zh-CN",
                    on_progress=None) -> list[str]:
    """Translate a list of strings from `source`, chunked under the endpoint's
    request-size limit. on_progress(done, total) is called after each chunk (and
    after each sentence of a chunk that had to fall back to one request per
    sentence) so callers can show real progress instead of 0/N → N/N (#756)."""
    t = _load(source, target)
    if t is None:
        return texts
    if not texts:
        return texts

    total = len(texts)
    out: list[str] = []
    done = 0

    def _report(extra: int = 0) -> None:
        """Report progress as `len(out) + extra` — out is the single source of
        truth for how many sentences are finished, so the fast path (whole chunk
        at once) and the per-sentence retry path can share one counter."""
        nonlocal done
        n = len(out) + extra
        if n != done:
            done = n
            if on_progress:
                on_progress(done, total)

    def _translate_and_report(chunk: list[str]) -> None:
        # In the retry path the chunk's own results aren't in `out` yet, so the
        # callback counts them via `extra`.
        pending = {"n": 0}

        def _on_item() -> None:
            pending["n"] += 1
            _report(pending["n"])

        out.extend(_translate_chunk(t, chunk, target, source, on_item=_on_item))
        _report()

    chunk: list[str] = []
    size = 0
    for text in texts:
        if chunk and size + len(text) > _CHUNK_CHAR_BUDGET:
            _translate_and_report(chunk)
            chunk, size = [], 0
        chunk.append(text)
        size += len(text) + 1
    if chunk:
        _translate_and_report(chunk)

    return out


# Legacy aliases kept for any callers that used the old API
def translate_zh_en(text: str) -> str:
    return translate_zh(text, target="en")
