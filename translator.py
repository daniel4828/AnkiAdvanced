"""
Local offline Chinese → English translation using argostranslate.

First run: downloads the zh→en model (~100 MB) from the argos-translate package index.
Subsequent runs: fully offline, no internet required.

Install: pip install argostranslate
"""
import logging

logger = logging.getLogger(__name__)

_translation = None  # cached Translation object


def _load() -> None:
    global _translation
    if _translation is not None:
        return

    try:
        from argostranslate import package, translate

        def _find_translation(installed):
            zh = next((l for l in installed if l.code == "zh"), None)
            en = next((l for l in installed if l.code == "en"), None)
            if zh and en:
                return zh.get_translation(en)
            return None

        _translation = _find_translation(translate.get_installed_languages())

        if _translation is None:
            logger.info("translator: zh→en model not found, downloading…")
            package.update_package_index()
            available = package.get_available_packages()
            pkg = next(
                (p for p in available if p.from_code == "zh" and p.to_code == "en"),
                None,
            )
            if pkg is None:
                raise RuntimeError("argostranslate: zh→en package not found in index")
            package.install_from_path(pkg.download())
            _translation = _find_translation(translate.get_installed_languages())

        if _translation is None:
            raise RuntimeError("argostranslate: zh→en translation not available after install")

        logger.info("translator: zh→en model loaded")

    except Exception as e:
        logger.error("translator: failed to load — %s", e)
        _translation = None


def translate_zh_en(text: str) -> str:
    """Translate a Chinese string to English. Returns original text on failure."""
    _load()
    if _translation is None:
        return text
    try:
        return _translation.translate(text)
    except Exception as e:
        logger.warning("translator: error — %s", e)
        return text


def translate_batch(texts: list[str]) -> list[str]:
    """Translate a list of Chinese strings to English."""
    return [translate_zh_en(t) for t in texts]
