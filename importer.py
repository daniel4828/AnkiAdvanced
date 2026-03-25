import json
import logging
import os
import re

import yaml

import database

logger = logging.getLogger(__name__)

# Maps YAML entry `type` → DB `note_type`. Unknown types are skipped.
NOTE_TYPE_MAP = {
    "vocabulary": "vocabulary",
    "sentence":   "sentence",
    "chengyu":    "chengyu",
    "expression": "expression",
}

_HANZI_RE = re.compile(r'[\u4e00-\u9fff\u3400-\u4dbf]')

# Fields compared when detecting component word conflicts
_CONFLICT_FIELDS = ("pinyin", "definition", "traditional")


def _format_yaml_error(e: yaml.YAMLError, filename: str = None) -> dict:
    """Return a structured, human-readable YAML error dict.

    Keys: file, line, column, problem, context, tip
    """
    result: dict = {}
    if filename:
        result["file"] = filename
    if hasattr(e, "problem_mark") and e.problem_mark is not None:
        result["line"] = e.problem_mark.line + 1
        result["column"] = e.problem_mark.column + 1
    if hasattr(e, "problem") and e.problem:
        result["problem"] = e.problem
    if hasattr(e, "context") and e.context:
        ctx = e.context
        if hasattr(e, "context_mark") and e.context_mark is not None:
            ctx += f" (line {e.context_mark.line + 1})"
        result["context"] = ctx

    # Attach a helpful tip for the most common mistake: unescaped quotes
    raw = str(e)
    if "scalar" in raw or "found" in raw:
        result["tip"] = (
            "If a value contains double quotes, wrap the whole value in single quotes. "
            'Example — change:  meaning: "lump meat" (a dish) '
            "→ to:  meaning: '\"lump meat\" (a dish)'"
        )

    result["raw"] = raw
    return result


def import_all(imports_dir: str = "imports") -> dict:
    """Recursively scan imports/<Source>/<optional subdirs>/*.yaml."""
    total_imported = 0
    total_skipped = 0
    total_invalid = 0
    for source_dir in sorted(os.scandir(imports_dir), key=lambda e: e.name):
        if not source_dir.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(source_dir.path):
            dirnames.sort()
            for filename in sorted(f for f in filenames if f.endswith(".yaml")):
                filepath = os.path.join(dirpath, filename)
                rel = os.path.relpath(dirpath, imports_dir)
                deck_path = rel.replace("\\", "/").split("/")
                result = import_yaml_file(filepath, deck_path)
                total_imported += result["imported"]
                total_skipped += result["skipped_duplicate"]
                total_invalid += result["skipped_invalid"]
    return {"imported": total_imported, "skipped_duplicate": total_skipped,
            "skipped_invalid": total_invalid}


def import_kouyu_yaml(filepath: str) -> dict:
    """Kept for backwards compatibility."""
    return import_yaml_file(filepath, ["Kouyu"])


def import_yaml_file(filepath: str, deck_path: list[str]) -> dict:
    """Parse one YAML file. deck_path is the folder hierarchy."""
    parent_id = None
    for segment in deck_path:
        parent_id = database.get_or_create_deck(segment, parent_id=parent_id)

    leaf_parent = deck_path[-1]
    deck_ids = _make_leaf_decks(leaf_parent, parent_id)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.error("YAML parse error in %s: %s", filepath, e)
        err = _format_yaml_error(e, filename=os.path.basename(filepath))
        return {"imported": 0, "skipped_duplicate": 0, "skipped_invalid": 0, "yaml_error": err}

    entries = _get_entries(data)
    source = deck_path[0].lower()
    return _import_entries(entries, deck_ids, source, label=os.path.basename(filepath))


def import_yaml_content(content: str, parent_deck_id: int,
                        resolutions: dict | None = None,
                        card_configs: dict | None = None,
                        custom_fields: dict | None = None) -> dict:
    """Import YAML from a string into an existing parent deck.

    resolutions:   {word_zh: "keep"|"update"|"custom"} for component word conflicts.
    card_configs:  {word_zh: {include, deck_path, suspended, ai_fill}}
    custom_fields: {word_zh: {pinyin, definition, traditional}} merged values for "custom" resolutions.
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        logger.error("YAML parse error in upload: %s", e)
        return {"imported": 0, "skipped_duplicate": 0, "skipped_invalid": 0,
                "yaml_error": _format_yaml_error(e)}

    parent = database.get_deck(parent_deck_id)
    leaf_parent = parent["name"] if parent else "Upload"
    default_deck_ids = _make_leaf_decks(leaf_parent, parent_deck_id)

    entries = _get_entries(data)
    source = leaf_parent.lower()
    return _import_entries(entries, default_deck_ids, source, label="<upload>",
                           resolutions=resolutions or {},
                           card_configs=card_configs or {},
                           custom_fields=custom_fields or {})


def preview_yaml_content(content: str) -> dict:
    """Parse YAML and return a preview + conflict list — no DB writes.

    Returns:
        {
          entries:   [{simplified, note_type, status, reason}],
          summary:   {ok, duplicate, invalid, unknown_type},
          conflicts: [{simplified, existing: {…}, incoming: {…}}]
        }
    """
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        return {
            "entries": [], "conflicts": [],
            "summary": {"ok": 0, "duplicate": 0, "invalid": 0, "unknown_type": 0},
            "error": str(e),
            "error_detail": _format_yaml_error(e),
        }

    entries = _get_entries(data)
    result_entries = []
    conflicts = []
    seen_conflicts = set()
    summary = {"ok": 0, "duplicate": 0, "invalid": 0, "unknown_type": 0}

    for entry in entries:
        yaml_type = entry.get("type", "")
        note_type = NOTE_TYPE_MAP.get(yaml_type)

        if note_type is None:
            summary["unknown_type"] += 1
            word_zh = entry.get("simplified", "").strip() or "(no simplified)"
            result_entries.append({
                "simplified": word_zh, "note_type": yaml_type or "(none)",
                "english": entry.get("english", ""),
                "hsk": str(entry.get("hsk", "") or ""),
                "status": "invalid", "reason": f"unknown type: {yaml_type!r}",
                "raw_yaml": yaml.dump(entry, allow_unicode=True, default_flow_style=False, sort_keys=False).strip(),
            })
            continue

        word_zh = entry.get("simplified", "").strip()
        if not word_zh:
            summary["invalid"] += 1
            result_entries.append({
                "simplified": "(empty)", "note_type": note_type,
                "english": "",
                "status": "invalid", "reason": "missing simplified field",
            })
            continue

        stripped = _strip_ellipsis(word_zh)
        if stripped != word_zh:
            logger.warning("STRIP preview: ellipsis stripped from %r → %r", word_zh, stripped)
            word_zh = stripped

        english = entry.get("english", "")
        hsk = str(entry.get("hsk", "") or "")
        warning = _validate_entry(word_zh, note_type)
        if warning:
            summary["invalid"] += 1
            result_entries.append({
                "simplified": word_zh, "note_type": note_type,
                "english": english, "hsk": hsk,
                "status": "invalid", "reason": warning,
                "raw_yaml": yaml.dump(entry, allow_unicode=True, default_flow_style=False, sort_keys=False).strip(),
            })
            continue

        raw = yaml.dump(entry, allow_unicode=True, default_flow_style=False, sort_keys=False).strip()
        existing = database.get_word_by_zh(word_zh)
        if existing and database.word_has_cards(existing["id"]):
            summary["duplicate"] += 1
            result_entries.append({
                "simplified": word_zh, "note_type": note_type,
                "english": english, "hsk": hsk,
                "status": "duplicate", "reason": None,
                "raw_yaml": raw,
            })
        else:
            summary["ok"] += 1
            result_entries.append({
                "simplified": word_zh, "note_type": note_type,
                "english": english, "hsk": hsk,
                "status": "ok", "reason": None,
                "raw_yaml": raw,
            })

        # Check component word_analyses for conflicts (char_only entries never conflict)
        for analysis in (entry.get("word_analyses") or []):
            if analysis.get("char_only"):
                continue
            if analysis.get("type") not in NOTE_TYPE_MAP:
                continue
            comp_zh = analysis.get("simplified", "").strip()
            if not comp_zh or comp_zh in seen_conflicts:
                continue
            comp_existing = database.get_word_by_zh(comp_zh)
            if comp_existing:
                incoming = _build_word_dict(analysis, source="")
                conflict_fields = {
                    f: (comp_existing.get(f), incoming.get(f))
                    for f in _CONFLICT_FIELDS
                    if comp_existing.get(f) != incoming.get(f)
                }
                if conflict_fields:
                    seen_conflicts.add(comp_zh)
                    conflicts.append({
                        "simplified": comp_zh,
                        "existing": {f: comp_existing.get(f) for f in _CONFLICT_FIELDS},
                        "incoming": {f: incoming.get(f) for f in _CONFLICT_FIELDS},
                    })

    return {"entries": result_entries, "summary": summary, "conflicts": conflicts}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_entries(data) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("entries") or data.get("vocab") or data.get("vocabulary") or []
    return []


def _make_leaf_decks(leaf_parent: str, parent_id: int) -> dict:
    return {
        "listening": database.get_or_create_deck(
            f"{leaf_parent} · Listening", parent_id=parent_id, category="listening"
        ),
        "reading": database.get_or_create_deck(
            f"{leaf_parent} · Reading", parent_id=parent_id, category="reading"
        ),
        "creating": database.get_or_create_deck(
            f"{leaf_parent} · Creating", parent_id=parent_id, category="creating"
        ),
    }


def _strip_ellipsis(word_zh: str) -> str:
    return word_zh.strip('…')


def _build_word_dict(entry: dict, source: str, note_type: str = "vocabulary") -> dict:
    return {
        "word_zh":         _strip_ellipsis(entry.get("simplified", "").strip()),
        "pinyin":          entry.get("pinyin"),
        "definition":      entry.get("english"),
        "pos":             entry.get("pos"),
        "hsk_level":       _hsk_to_int(str(entry.get("hsk", ""))),
        "traditional":     entry.get("traditional"),
        "definition_zh":   entry.get("definition_zh"),
        "source":          source,
        "note_type":       note_type,
        "source_sentence": entry.get("source_de"),
        "grammar_notes":   entry.get("grammar_de"),
    }


def _process_characters(entry: dict, word_id: int) -> None:
    """Insert characters from an entry's `characters` list and link to word."""
    for pos, char_entry in enumerate(entry.get("characters") or []):
        char_text = char_entry.get("char", "").strip()
        if not char_text:
            continue
        detailed = char_entry.get("detailed_analysis", False)
        other_meanings = char_entry.get("other_meanings")
        compounds_raw  = char_entry.get("compounds")
        char_dict = {
            "char":           char_text,
            "traditional":    char_entry.get("traditional"),
            "pinyin":         char_entry.get("pinyin"),
            "hsk_level":      _hsk_to_int(str(char_entry.get("hsk", ""))),
            "etymology":      char_entry.get("etymology") if detailed else None,
            "other_meanings": json.dumps(other_meanings, ensure_ascii=False)
                              if other_meanings else None,
            "compounds":      json.dumps(compounds_raw, ensure_ascii=False)
                              if compounds_raw else None,
        }
        char_id = database.upsert_character(char_dict)
        database.insert_word_character(
            word_id=word_id,
            char_id=char_id,
            position=pos,
            meaning_in_context=char_entry.get("meaning_in_context") if detailed else None,
        )


def _process_char_only_component(analysis: dict, note_word_id: int,
                                 position: int, source: str) -> None:
    """Store a char_only word_analyses entry as a minimal word and link it."""
    char_text = analysis.get("char_only", "").strip()
    if not char_text:
        return
    comp_word = {
        "word_zh":         char_text,
        "pinyin":          analysis.get("pinyin"),
        "definition":      None,
        "pos":             None,
        "hsk_level":       _hsk_to_int(str(analysis.get("hsk", ""))),
        "traditional":     None,
        "definition_zh":   None,
        "source":          source,
        "note_type":       "vocabulary",
        "source_sentence": None,
        "grammar_notes":   None,
    }
    comp_word_id = database.insert_word(comp_word)
    database.insert_note_component(note_word_id, comp_word_id, position)


def _process_component(analysis: dict, note_word_id: int, position: int,
                       source: str, resolutions: dict,
                       custom_fields: dict | None = None) -> None:
    """Store a word_analyses component word and link it to its parent note."""
    comp_zh = analysis.get("simplified", "").strip()
    if not comp_zh:
        return

    comp_word = _build_word_dict(analysis, source=source, note_type="vocabulary")
    comp_word_id = database.insert_word(comp_word)  # INSERT OR IGNORE

    resolution = resolutions.get(comp_zh, "keep")
    if resolution == "update":
        database.update_word(comp_word_id, comp_word)
    elif resolution == "custom" and custom_fields and comp_zh in custom_fields:
        merged = {**comp_word, **(custom_fields[comp_zh] or {})}
        database.update_word(comp_word_id, merged)

    _process_characters(analysis, comp_word_id)

    # Store examples if present
    for i, ex in enumerate(analysis.get("examples") or []):
        database.insert_word_example(
            word_id=comp_word_id,
            example_zh=ex.get("zh", ""),
            example_pinyin=ex.get("pinyin"),
            example_de=ex.get("de"),
            position=i,
        )

    database.insert_note_component(note_word_id, comp_word_id, position)


_sentences_deck_ids_cache: dict | None = None


def _get_sentences_deck_ids() -> dict:
    global _sentences_deck_ids_cache
    if _sentences_deck_ids_cache is None:
        _sentences_deck_ids_cache = database.get_sentences_deck_ids()
    return _sentences_deck_ids_cache


def _import_entries(entries: list, deck_ids: dict, source: str, label: str,
                    resolutions: dict | None = None,
                    card_configs: dict | None = None,
                    custom_fields: dict | None = None) -> dict:
    if resolutions is None:
        resolutions = {}
    if card_configs is None:
        card_configs = {}
    if custom_fields is None:
        custom_fields = {}

    imported = 0
    skipped_duplicate = 0
    skipped_invalid = 0
    skipped_entries: list[dict] = []
    _deck_path_cache: dict[str, dict] = {}  # deck_path → leaf deck_ids

    for entry in entries:
        yaml_type = entry.get("type", "")
        note_type = NOTE_TYPE_MAP.get(yaml_type)

        if note_type is None:
            logger.debug("SKIP %s: unknown type %r", label, yaml_type)
            continue

        word_zh = entry.get("simplified", "").strip()
        if not word_zh:
            skipped_invalid += 1
            continue

        stripped = _strip_ellipsis(word_zh)
        if stripped != word_zh:
            logger.warning("STRIP %s: ellipsis stripped from %r → %r", label, word_zh, stripped)
            word_zh = stripped

        warning = _validate_entry(word_zh, note_type)
        if warning:
            logger.warning("SKIP %s: %s", label, warning)
            skipped_invalid += 1
            skipped_entries.append({
                "word": word_zh, "reason": warning,
                "raw_yaml": yaml.dump(entry, allow_unicode=True, default_flow_style=False, sort_keys=False).strip(),
            })
            continue

        # Per-card config (frontend overrides)
        card_cfg = card_configs.get(word_zh, {})

        # Respect per-card include flag (defaults to True)
        if not card_cfg.get("include", True):
            logger.debug("SKIP %s: excluded by user config", word_zh)
            continue

        # Sentences always go into the dedicated Sentences deck regardless of source
        if note_type == "sentence":
            target_deck_ids = _get_sentences_deck_ids()
            logger.info("SENTENCE %s: %r → Sentences deck", label, word_zh)
        else:
            # Resolve per-card deck path override
            card_deck_path = card_cfg.get("deck_path")
            if card_deck_path:
                if card_deck_path not in _deck_path_cache:
                    try:
                        pid = database.get_or_create_deck_path(card_deck_path)
                        parent = database.get_deck(pid)
                        leaf_name = parent["name"] if parent else card_deck_path.split("::")[-1]
                        _deck_path_cache[card_deck_path] = _make_leaf_decks(leaf_name, pid)
                    except Exception as e:
                        logger.warning("deck_path %r failed (%s), using default", card_deck_path, e)
                        _deck_path_cache[card_deck_path] = deck_ids
                target_deck_ids = _deck_path_cache[card_deck_path]
            else:
                target_deck_ids = deck_ids

        word = _build_word_dict(entry, source=source, note_type=note_type)
        word_id = database.insert_word(word)  # INSERT OR IGNORE → always get id

        if database.word_has_cards(word_id):
            skipped_duplicate += 1
            skipped_entries.append({"word": word_zh, "reason": "already in deck"})
            # Still process word_analyses so components stay linked
            for pos, analysis in enumerate(entry.get("word_analyses") or []):
                if analysis.get("char_only"):
                    _process_char_only_component(analysis, word_id, pos, source)
                elif analysis.get("type") in NOTE_TYPE_MAP:
                    _process_component(analysis, word_id, pos, source, resolutions, custom_fields)
            continue

        # Examples
        for i, ex in enumerate(entry.get("examples") or []):
            database.insert_word_example(
                word_id=word_id,
                example_zh=ex.get("zh", ""),
                example_pinyin=ex.get("pinyin"),
                example_de=ex.get("de"),
                position=i,
            )

        # Characters
        _process_characters(entry, word_id)

        # Component word_analyses (sentences / chengyu / expressions)
        for pos, analysis in enumerate(entry.get("word_analyses") or []):
            if analysis.get("char_only"):
                _process_char_only_component(analysis, word_id, pos, source)
            elif analysis.get("type") in NOTE_TYPE_MAP:
                _process_component(analysis, word_id, pos, source, resolutions)

        suspended_states = card_cfg.get("suspended") or None
        _create_cards(word_id, target_deck_ids, suspended_states)
        imported += 1

    return {"imported": imported, "skipped_duplicate": skipped_duplicate,
            "skipped_invalid": skipped_invalid, "skipped_entries": skipped_entries}


def _validate_entry(word_zh: str, note_type: str) -> str | None:
    """Return a warning string if the entry is invalid, else None."""
    if note_type == "sentence":
        if '/' in word_zh or '／' in word_zh:
            return f"slash in sentence: {word_zh!r}"
        return None
    # vocabulary / chengyu: strict rules
    if '/' in word_zh or '／' in word_zh:
        return f"slash in word (multiple entries combined): {word_zh!r}"
    if '。' in word_zh or '. ' in word_zh:
        return f"period in word (looks like a sentence): {word_zh!r}"
    return None


# Default per-category suspension: reading/listening active, creating suspended
_DEFAULT_SUSPENDED: dict[str, bool] = {
    "reading": False,
    "listening": False,
    "creating": True,
}


def _create_cards(word_id: int, deck_ids: dict,
                  suspended_states: dict[str, bool] | None = None) -> None:
    if suspended_states is None:
        suspended_states = _DEFAULT_SUSPENDED
    for category, deck_id in deck_ids.items():
        is_suspended = suspended_states.get(category,
                                            _DEFAULT_SUSPENDED.get(category, False))
        state = "suspended" if is_suspended else "new"
        database.insert_card(word_id, category, deck_id, state=state)


def _hsk_to_int(hsk_str: str) -> int | None:
    if not hsk_str:
        return None
    s = str(hsk_str).strip()
    if s in ("超纲", ""):
        return None
    try:
        return int(s)
    except ValueError:
        return None


# Keep old name as alias
_kouyu_hsk_to_int = _hsk_to_int
