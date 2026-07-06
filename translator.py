"""
Source language → target language translation using Google Translate (via deep_translator).

The source language is configurable (defaults to Chinese, "zh-CN") so this module
can also translate other learner languages (e.g. French) into German.

Requires internet access (VPN recommended in China).
Install: pip install deep-translator
"""
import logging

logger = logging.getLogger(__name__)

_translators: dict[tuple[str, str], object] = {}


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
        return t.translate(text) or text
    except Exception as e:
        logger.warning("translator: error (source=%s, target=%s) — %s", source, target, e)
        return text


def translate_batch(texts: list[str], target: str = "en", source: str = "zh-CN") -> list[str]:
    """Translate a list of strings from `source` in a single HTTP request."""
    t = _load(source, target)
    if t is None:
        return texts
    if not texts:
        return texts

    sep = "\n"
    combined = sep.join(text.strip() or " " for text in texts)
    try:
        translated = t.translate(combined) or combined
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
