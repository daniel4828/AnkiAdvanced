"""自定义提示词模板测试（issue #581）。

核心保证：默认模板经 _render_prompt 渲染后与重构前的内联 f-string 逐字一致
（提示词质量不因重构漂移），以及 DB 覆盖/重置往返正确。
"""
import os

import pytest

os.environ.setdefault("DISABLE_AI", "1")

import ai
import database
import database.core


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(database.core, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()


def _old_story_prompt(word_list, max_hsk, grammar_first, topic):
    """重构前 generate_story zh 分支的 story 模式 f-string（逐字复制）。"""
    task_line = "Write a short Mandarin Chinese story to help an HSK 4-5 learner review vocabulary."
    topic_clause = f"- The story should be set around this topic or theme: {topic}\n" if topic else ""
    style_rule = f"{topic_clause}- The sentences must form a coherent narrative with the same recurring characters"
    return f"""{task_line}

{grammar_first}Target words (each must appear verbatim in at least one sentence):
{word_list}

Rules:
- Each target word MUST appear verbatim in at least one sentence
- Write the sentences in the same order as the target word list above
- For items marked [SENTENCE]: use that exact text as the sentence, unchanged
- Use proper Chinese punctuation — include commas（，）where natural pauses occur
- Use only HSK 1-{max_hsk} vocabulary for non-target words; each sentence must contain exactly ONE target word from the list — do not use other target words from the list in that sentence
- Keep each sentence short and simple
{style_rule}
- NEVER highlight, quote, or mark target words in any way — no "quotes", no 「brackets」, no （parentheses）, no bold, no underline; write them as plain text embedded naturally in the sentence
- NEVER use markdown formatting (**bold**, _italic_, etc.) anywhere in the output — write plain text only

Return ONLY a numbered list of Chinese sentences, no explanation:
1. ...
2. ..."""


def _render_story(word_list, max_hsk, grammar_first, topic):
    return ai._render_prompt(ai.DEFAULT_PROMPT_TEMPLATES["story"], {
        "grammar_block": grammar_first,
        "words": word_list,
        "max_hsk": str(max_hsk),
        "topic_block": (
            f"- The story should be set around this topic or theme: {topic}\n" if topic else ""
        ),
    })


def test_story_template_matches_old_prompt_no_topic():
    wl = "1. 蘑菇\n2. [SENTENCE] 他把书放在桌子上。"
    assert _render_story(wl, 3, "", None) == _old_story_prompt(wl, 3, "", None)


def test_story_template_matches_old_prompt_with_topic_and_grammar():
    wl = "1. 蘑菇"
    grammar = "GRAMMAR FOCUS: Use the pattern 「把字句」 in roughly 1 of the sentences (about 75%).\n\n"
    assert _render_story(wl, 5, grammar, "咖啡店") == _old_story_prompt(wl, 5, grammar, "咖啡店")


def test_podcast_template_keeps_json_example_braces():
    rendered = ai._render_prompt(ai.DEFAULT_PROMPT_TEMPLATES["podcast"], {
        "title": "T", "summary": "S", "words": "1. 蘑菇（mógū）— mushroom",
        "max_hsk": "3", "extra_hint": "",
    })
    # JSON 示例的花括号必须原样保留（替换只针对已知记号）
    assert '{"sentence_zh": "含目标词的句子", "target_word": "词汇"}' in rendered
    assert "T" in rendered and "S" in rendered and "HSK 1-3" in rendered
    # 所有记号都已被替换
    for var in ai.PROMPT_TEMPLATE_VARIABLES["podcast"]:
        assert "{" + var + "}" not in rendered


def test_custom_template_roundtrip(db):
    assert database.get_prompt_template("story") is None
    database.set_prompt_template("story", "MY TEMPLATE {words}")
    assert database.get_prompt_template("story") == "MY TEMPLATE {words}"
    assert ai._story_prompt_template("story") == "MY TEMPLATE {words}"
    database.set_prompt_template("story", "V2 {words}")  # upsert
    assert database.get_prompt_template("story") == "V2 {words}"
    database.delete_prompt_template("story")
    assert database.get_prompt_template("story") is None
    assert ai._story_prompt_template("story") == ai.DEFAULT_PROMPT_TEMPLATES["story"]


def test_every_template_declares_words_variable():
    for mode, tpl in ai.DEFAULT_PROMPT_TEMPLATES.items():
        assert "{words}" in tpl, mode
        for var in ai.PROMPT_TEMPLATE_VARIABLES[mode]:
            assert "{" + var + "}" in tpl, f"{mode} missing {{{var}}}"
