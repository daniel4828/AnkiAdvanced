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

# Google Translate rejects inputs over 5000 characters. Stay well under it so a
# batch never trips the limit and falls back to one-request-per-sentence (#758).
_MAX_REQUEST_CHARS = 4500


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


def _chunk_by_chars(texts: list[str], limit: int) -> list[list[str]]:
    """Split texts into groups whose newline-joined length stays under `limit`.
    A single over-long text still gets its own group — nothing is dropped."""
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    for text in texts:
        cost = len(text) + 1  # +1 for the separator
        if current and size + cost > limit:
            chunks.append(current)
            current, size = [], 0
        current.append(text)
        size += cost
    if current:
        chunks.append(current)
    return chunks


def translate_batch(texts: list[str], target: str = "en", source: str = "zh-CN") -> list[str]:
    """Translate a list of strings from `source`, batching them into as few HTTP
    requests as Google's 5000-character input limit allows.

    Sending everything in one request used to blow past that limit for long
    stories, and the resulting fallback was one request *per sentence* — 228
    serial calls that looked like a total freeze from the UI (#758). Chunking
    keeps the common case at a handful of requests, and a failed chunk only
    degrades its own sentences."""
    t = _load(source, target)
    if t is None or not texts:
        return texts

    results: list[str] = []
    for chunk in _chunk_by_chars(texts, _MAX_REQUEST_CHARS):
        results.extend(_translate_chunk(t, chunk, target, source))
    return results


def _translate_chunk(t, texts: list[str], target: str, source: str) -> list[str]:
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

    return [translate_zh(text, target, source) for text in texts]


# Legacy aliases kept for any callers that used the old API
def translate_zh_en(text: str) -> str:
    return translate_zh(text, target="en")
