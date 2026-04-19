"""
Chinese → target language translation using Google Translate (via deep_translator).

Requires internet access (VPN recommended in China).
Install: pip install deep-translator
"""
import logging

logger = logging.getLogger(__name__)

_translators: dict[str, object] = {}


def _load(target: str) -> object | None:
    if target in _translators:
        return _translators[target]
    try:
        from deep_translator import GoogleTranslator
        t = GoogleTranslator(source="zh-CN", target=target)
        _translators[target] = t
        logger.info("translator: GoogleTranslator loaded (target=%s)", target)
        return t
    except Exception as e:
        logger.error("translator: failed to load (target=%s) — %s", target, e)
        _translators[target] = None
        return None


def translate_zh(text: str, target: str = "en") -> str:
    """Translate a Chinese string to the target language. Returns original on failure."""
    t = _load(target)
    if t is None or not text.strip():
        return text
    try:
        return t.translate(text) or text
    except Exception as e:
        logger.warning("translator: error (target=%s) — %s", target, e)
        return text


def translate_batch(texts: list[str], target: str = "en") -> list[str]:
    """Translate a list of Chinese strings in a single HTTP request."""
    t = _load(target)
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
        logger.warning("translator: batch error (target=%s) — %s", target, e)

    return [translate_zh(text, target) for text in texts]


# Legacy aliases kept for any callers that used the old API
def translate_zh_en(text: str) -> str:
    return translate_zh(text, target="en")
