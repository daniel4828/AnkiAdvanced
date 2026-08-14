"""提示词模板/版本库测试（issue #581 引入模板，#610 升级为多命名版本）。

核心保证：默认模板经 _render_prompt 渲染后与重构前的内联 f-string 逐字一致
（提示词质量不因重构漂移）；prompt_presets 的增删改查、生效切换往返正确；
旧 prompt_templates 数据能幂等迁移为名为 "Saved" 的生效版本。
"""
import os
import sqlite3

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


def test_knowledge_template_keeps_json_example_braces():
    """issue #654: 模板键从 "podcast" 改名为 "knowledge"（故事模式标识符同步改名）。"""
    rendered = ai._render_prompt(ai.DEFAULT_PROMPT_TEMPLATES["knowledge"], {
        "title": "T", "summary": "S", "words": "1. 蘑菇（mógū）— mushroom",
        "max_hsk": "3", "extra_hint": "",
    })
    # JSON 示例的花括号必须原样保留（替换只针对已知记号）。断言写在结构上而不是
    # 示例的逐字文本上——提示词本身会被反复调（#737/#741），逐字断言只会逼着
    # 每次调词都来改测试，守不住任何真实契约。
    assert '{"reasoning_zh":' in rendered
    assert '"sentence_zh":' in rendered and '"target_word":' in rendered
    assert "T" in rendered and "S" in rendered and "HSK 1-3" in rendered
    # 所有记号都已被替换
    for var in ai.PROMPT_TEMPLATE_VARIABLES["knowledge"]:
        assert "{" + var + "}" not in rendered


def test_podcast_prompt_presets_migrate_to_knowledge(db):
    """issue #654: prompt_presets 里 mode='podcast' 的自定义版本要迁移到
    'knowledge'，且迁移必须幂等（生产库每 2 分钟自动跑一次 init_db）。"""
    conn = database.get_db()
    conn.execute(
        """INSERT INTO prompt_presets (mode, name, template, is_active, updated_at)
           VALUES ('podcast', 'Saved', 'MY PODCAST TEMPLATE {words}', 1, datetime('now'))"""
    )
    conn.commit()
    conn.close()

    database.init_db()  # re-run the migration (simulates deploy.sh's 2-minute cron)

    assert database.get_prompt_template("podcast") is None
    assert database.get_prompt_template("knowledge") == "MY PODCAST TEMPLATE {words}"

    # Idempotent: running init_db() again must not error or duplicate rows.
    database.init_db()
    presets = database.list_prompt_presets("knowledge")
    assert len(presets) == 1


def test_custom_template_roundtrip(db):
    """创建一个 preset 就地生效；重命名/改内容不改变生效状态；删除后回落默认。"""
    assert database.get_prompt_template("story") is None
    preset_id = database.create_prompt_preset("story", "V1", "MY TEMPLATE {words}")
    assert database.get_prompt_template("story") == "MY TEMPLATE {words}"
    assert ai._story_prompt_template("story") == "MY TEMPLATE {words}"

    database.update_prompt_preset(preset_id, template="V2 {words}")
    assert database.get_prompt_template("story") == "V2 {words}"

    database.delete_prompt_preset(preset_id)
    assert database.get_prompt_template("story") is None
    assert ai._story_prompt_template("story") == ai.DEFAULT_PROMPT_TEMPLATES["story"]


def test_create_preset_sets_active_and_deactivates_others(db):
    p1 = database.create_prompt_preset("story", "Detective", "D {words}")
    assert database.get_prompt_template("story") == "D {words}"
    p2 = database.create_prompt_preset("story", "Everyday", "E {words}")
    # Creating a second preset activates it and deactivates the first.
    assert database.get_prompt_template("story") == "E {words}"
    presets = {p["id"]: p for p in database.list_prompt_presets("story")}
    assert presets[p1]["is_active"] == 0
    assert presets[p2]["is_active"] == 1


def test_activate_preset_switches_which_is_active(db):
    p1 = database.create_prompt_preset("story", "Detective", "D {words}")
    p2 = database.create_prompt_preset("story", "Everyday", "E {words}")
    database.activate_prompt_preset(p1)
    assert database.get_prompt_template("story") == "D {words}"
    presets = {p["id"]: p for p in database.list_prompt_presets("story")}
    assert presets[p1]["is_active"] == 1
    assert presets[p2]["is_active"] == 0


def test_rename_preset(db):
    p1 = database.create_prompt_preset("story", "Detective", "D {words}")
    database.update_prompt_preset(p1, name="Renamed")
    preset = database.get_prompt_preset(p1)
    assert preset["name"] == "Renamed"
    assert preset["template"] == "D {words}"  # untouched


def test_duplicate_preset_name_raises(db):
    database.create_prompt_preset("story", "Detective", "D {words}")
    with pytest.raises(sqlite3.IntegrityError):
        database.create_prompt_preset("story", "Detective", "Other {words}")


def test_presets_scoped_per_mode(db):
    database.create_prompt_preset("story", "Same Name", "S {words}")
    # Same name in a different mode is fine — UNIQUE is (mode, name).
    database.create_prompt_preset("qa", "Same Name", "Q {words}")
    assert database.get_prompt_template("story") == "S {words}"
    assert database.get_prompt_template("qa") == "Q {words}"


def test_delete_active_preset_falls_back_to_default(db):
    p1 = database.create_prompt_preset("story", "Detective", "D {words}")
    database.delete_prompt_preset(p1)
    assert database.get_prompt_template("story") is None
    assert ai._story_prompt_template("story") == ai.DEFAULT_PROMPT_TEMPLATES["story"]


def test_deactivate_prompt_presets_keeps_saved_versions(db):
    """DELETE /api/prompt-template/{mode} 语义（#610）：取消生效但不删除版本。"""
    p1 = database.create_prompt_preset("story", "Detective", "D {words}")
    database.deactivate_prompt_presets("story")
    assert database.get_prompt_template("story") is None
    # Preset still exists and can be re-activated.
    assert database.get_prompt_preset(p1) is not None
    database.activate_prompt_preset(p1)
    assert database.get_prompt_template("story") == "D {words}"


def test_migration_from_old_prompt_templates_table(db):
    """旧 prompt_templates 行迁移为名为 Saved 的生效 preset；再次 init_db 不重复插入。"""
    conn = database.core.get_db()
    conn.execute(
        "INSERT INTO prompt_templates (mode, template) VALUES (?, ?)",
        ("expository", "OLD CUSTOM {words}"),
    )
    conn.commit()
    conn.close()

    database.init_db()
    assert database.get_prompt_template("expository") == "OLD CUSTOM {words}"
    presets = database.list_prompt_presets("expository")
    assert len(presets) == 1
    assert presets[0]["name"] == "Saved"
    assert presets[0]["is_active"] == 1

    # Running init_db() again must not duplicate the migrated preset, even
    # though the old prompt_templates row is still there (never deleted).
    database.init_db()
    presets_again = database.list_prompt_presets("expository")
    assert len(presets_again) == 1


def test_every_template_declares_words_variable():
    for mode, tpl in ai.DEFAULT_PROMPT_TEMPLATES.items():
        assert "{words}" in tpl, mode
        for var in ai.PROMPT_TEMPLATE_VARIABLES[mode]:
            assert "{" + var + "}" in tpl, f"{mode} missing {{{var}}}"
