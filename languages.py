"""
Language registry — the single source of truth for per-language behavior.

Every language the app supports gets one entry here. Adding a new language
means adding one dict entry (plus an importer YAML format if it needs one) —
no other module should hard-code language-specific values.

Consumers (wired up across PRs #428–#431):
  - tts.py             → tts_voice / say_voice
  - translator.py      → translator_source
  - routes/story.py    → tokenizer (jieba vs. whitespace)
  - ai.py              → prompt fragments (language_name, learner_level,
                          background_vocab, sentence_limit)
  - static/app.js      → features (which UI elements to show per deck)
"""

DEFAULT_LANG = "zh"

LANGUAGES = {
    "zh": {
        "code": "zh",
        "name_en": "Mandarin Chinese",     # language name used inside AI prompts
        "name_native": "中文",
        # ── TTS ──
        "tts_voice": "zh-CN-XiaoxiaoNeural",   # edge-tts voice
        "say_voice": "Tingting",               # macOS `say` fallback voice
        # ── Translation (deep-translator source code) ──
        "translator_source": "zh-CN",
        # ── Story tokenization for click-to-lookup: 'jieba' | 'whitespace' ──
        "tokenizer": "jieba",
        # ── AI prompt fragments ──
        "level_system": "HSK",
        "learner_level": "HSK 4-5",            # the learner's level
        "background_vocab": "HSK 1-2",         # default level cap for non-target words
        "sentence_limit": "15 Chinese characters",
        # ── Language-specific features (drive schema usage + frontend UI) ──
        "features": {
            "pinyin": True,
            "characters": True,        # per-character breakdown (汉字)
            "measure_words": True,     # 量词
            "traditional": True,
            # news/kahneman/paste/briefing story modes are zh-only for now
            "extended_story_modes": True,
        },
    },
    "fr": {
        "code": "fr",
        "name_en": "French",
        "name_native": "français",
        "tts_voice": "fr-FR-DeniseNeural",
        "say_voice": "Thomas",
        "translator_source": "fr",
        "tokenizer": "whitespace",
        "level_system": "CEFR",
        "learner_level": "CEFR B1",            # Daniel's French level (2026-07-06)
        "background_vocab": "CEFR A1-A2",
        "sentence_limit": "12 words",
        "features": {
            "pinyin": False,
            "characters": False,
            "measure_words": False,
            "traditional": False,
            "extended_story_modes": False,
        },
    },
}


def get_lang_config(lang: str | None) -> dict:
    """Return the config for `lang`, falling back to the default language.

    Unknown/legacy values fall back to zh so old rows can never crash the app.
    """
    return LANGUAGES.get(lang or DEFAULT_LANG, LANGUAGES[DEFAULT_LANG])


def is_valid_lang(lang: str | None) -> bool:
    return lang in LANGUAGES
