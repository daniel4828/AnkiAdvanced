"""Knowledge-base vocabulary annotation dispatch (#804).

Every language the app knows about has one entry in languages.py that names
an "annotator" implementation (a family-level field — see languages.py's
_SINITIC_BASE/_ROMANCE_BASE). annotate_summary() is the single call site
knowledge/rendition.py (and, for completeness, anything else that wants an
annotated summary) goes through, so a new language only ever needs a new
languages.py entry pointing at an existing annotator, never a new dispatch
site.

  "zh"      -> zh_annotate.py (#638): zero-AI, HSK-table + jieba + pypinyin.
               Untouched by #804 — wrapped here, not modified.
  "romance" -> annotate/romance.py (#804): entry_forms exact-match lookup
               (no stemming) + a per-language stopword list + Google
               Translate glosses for whatever's left.
"""
import logging

import languages

logger = logging.getLogger(__name__)


def annotate_summary(text: str, lang: str) -> tuple[str, list[dict]]:
    """(annotated_text, new_words) for `text` in `lang`. new_words is a list
    of dicts (shape varies slightly per annotator — see the individual
    implementations) in order of first appearance.

    Never raises: an annotator failure, or an unrecognized "annotator" name
    (should not happen — every registered language in languages.py names one
    of the two below), degrades to the text unannotated with no new words.
    That mirrors the "a missing gloss is a minor inconvenience" contract
    each individual annotator already promises for its own internal
    failures."""
    if not text or not text.strip():
        return text, []
    annotator = languages.get_lang_config(lang).get("annotator")
    try:
        if annotator == "zh":
            import zh_annotate
            annotated = zh_annotate.annotate_zh_summary(text)
            new_words = zh_annotate.extract_new_words(text)
            return annotated, new_words
        if annotator == "romance":
            from . import romance
            return romance.annotate_summary(text, lang)
        logger.warning("annotate: unknown annotator %r for lang=%s", annotator, lang)
    except Exception as e:
        logger.warning("annotate: dispatch failed for lang=%s — %s", lang, e)
    return text, []
