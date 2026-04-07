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
    """Translate a list of Chinese strings to English."""
    _load()
    if _translator is None:
        return texts
    try:
        from deep_translator import GoogleTranslator
        results = GoogleTranslator(source="zh-CN", target="en").translate_batch(texts)
        return [r or t for r, t in zip(results, texts)]
    except Exception as e:
        logger.warning("translator: batch error — %s", e)
        return [translate_zh_en(t) for t in texts]
