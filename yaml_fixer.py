"""yaml_fixer.py — Pre-process AI-generated YAML to fix common formatting errors.

DeepSeek and other AI models frequently produce YAML with two recurring errors in
plain inline-string fields:

  1. Unescaped double quotes inside a double-quoted value:
         english: "to "escape" from reality"   ← invalid YAML
     Fixed: english: 'to "escape" from reality'

  2. A bare colon-space (": ") inside an unquoted value:
         english: happiness is simple: know contentment   ← invalid YAML
     Fixed: english: 'happiness is simple: know contentment'

Because we know the YAML schema (which fields are always plain strings), we can
detect and fix both patterns before handing the content to yaml.safe_load().
"""

import re

# Fields whose values are always plain inline strings — never block scalars,
# lists, or nested mappings.  These are the fields where AI models introduce
# stray inner double quotes or bare colons.
_INLINE_STRING_FIELDS: frozenset[str] = frozenset({
    "english",
    "german",
    "de",             # short form of german used in examples / similar_sentences / relations
    "definition_zh",
    "source_de",
    "register",
    "pos",
    "meaning_in_context",
    "meaning",        # used in compounds, synonyms, antonyms, measure_word
    "structure",      # grammar entries
    "explanation",    # grammar entries
    "cultural_note",  # grammar entries
    "pattern",        # grammar entries
    "title",          # grammar comparison entries
})

# Matches a YAML block-context key-value line.
# Captures: (indent)(key): (value)
# Skips lines whose value starts with a block-scalar indicator (|, >),
# sequence (-), flow mapping/sequence ({, [), or comment (#).
_LINE_RE = re.compile(r'^(\s*)(\w+):\s+([^|>\[{#\n].+)$')


def _is_problematic(value: str) -> bool:
    """Return True if *value* would cause a YAML parse error in a block mapping."""
    # Already single-quoted — YAML single-quote syntax is robust, leave it alone.
    if value.startswith("'"):
        return False

    # Double-quoted: problematic if it contains an unescaped inner " character.
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        inner = value[1:-1]
        i = 0
        while i < len(inner):
            if inner[i] == '\\':
                i += 2  # skip \X escape sequence
                continue
            if inner[i] == '"':
                return True
            i += 1
        return False

    # Unquoted: problematic if it contains ": " (colon-space), which YAML
    # interprets as the start of a new mapping value in block context.
    if ': ' in value or value.endswith(':'):
        return True

    return False


def _requote(value: str) -> str:
    """Normalise *value* to a safe YAML single-quoted string.

    Strips outer double quotes if present, unescapes \\" sequences, then
    wraps the result in single quotes (escaping inner ' as '').
    """
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        inner = value[1:-1]
        inner = inner.replace('\\"', '"')  # unescape \" → "
    else:
        inner = value
    inner = inner.replace("'", "''")  # YAML single-quote escape
    return f"'{inner}'"


def fix_yaml_content(content: str) -> str:
    """Fix common AI-generated YAML errors and return the corrected string.

    Scans the content line by line.  For each known inline-string field, if the
    value would cause a YAML parse error it is re-wrapped in safe single quotes.

    Returns the original string unchanged when no fixes are needed.
    """
    lines = content.split('\n')
    changed = False
    result = []

    for line in lines:
        m = _LINE_RE.match(line)
        if m:
            indent, key, value = m.group(1), m.group(2), m.group(3).rstrip()
            if key in _INLINE_STRING_FIELDS and _is_problematic(value):
                fixed_value = _requote(value)
                line = f"{indent}{key}: {fixed_value}"
                changed = True
        result.append(line)

    return '\n'.join(result) if changed else content
