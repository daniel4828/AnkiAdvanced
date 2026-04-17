"""
Chinese → English translation using Google Translate (via deep_translator).

Requires internet access (VPN recommended in China).
Install: pip install deep-translator
"""
import logging

logger = logging.getLogger(__name__)

_translator = None


def _load() -> None:
    global _translator
    if _translator is not None:
        return
    try:
        from deep_translator import GoogleTranslator
        _translator = GoogleTranslator(source="zh-CN", target="en")
        logger.info("translator: GoogleTranslator loaded")
    except Exception as e:
        logger.error("translator: failed to load — %s", e)
        _translator = None


def translate_zh_en(text: str) -> str:
    """Translate a Chinese string to English. Returns original text on failure."""
    _load()
    if _translator is None or not text.strip():
        return text
    try:
        return _translator.translate(text) or text
    except Exception as e:
        logger.warning("translator: error — %s", e)
        return text


def translate_batch(texts: list[str]) -> list[str]:
    """Translate a list of Chinese strings to English in a single HTTP request."""
    _load()
    if _translator is None:
        return texts
    if not texts:
        return texts

    # Join with newline — Google Translate preserves \n, so one request suffices.
    sep = "\n"
    combined = sep.join(t.strip() or " " for t in texts)
    try:
        translated = _translator.translate(combined) or combined
        parts = translated.split(sep)
        # If split count matches, return aligned results; otherwise fall back.
        if len(parts) == len(texts):
            return [p.strip() or t for p, t in zip(parts, texts)]
        logger.warning("translator: split count mismatch (%d vs %d), falling back", len(parts), len(texts))
    except Exception as e:
        logger.warning("translator: batch error — %s", e)

    # Fallback: translate one by one
    return [translate_zh_en(t) for t in texts]
