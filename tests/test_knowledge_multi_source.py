"""知识库故事模式的多素材生成（issue #776，取代 #752 的混合生成方式）：选中
多份素材（播客/视频/文章）时，到期词表在素材之间平均切分，每份素材各自发起
一次独立的 AI 调用，句子按素材顺序拼接——读起来是"先讲完第一份素材，
再讲第二份"，而不是像 #752 那样在同一次调用里让模型交替引用多份素材。

覆盖：
- routes.story._parse_episode_ids 的输入解析（不变）
- 词表在素材间平均切分，余数分给靠前的素材
- 调用次数等于有材料的素材数（batch_size 未设时）；每次调用只收到一个
  source dict，且 material 是完整的 _KNOWLEDGE_MATERIAL_MAX_CHARS 预算
  （不再像 #752 那样按素材数量均分）
- 返回的句子顺序：素材 1 的词全部排在素材 2 之前
- 没有材料的素材被跳过、未知 id 报错、全部为空报错
- 单素材路径下提示词里不能出现任何 #752 遗留的多素材痕迹
  （source_index / multi_source_block / "素材 N" 等）

AI 一律打桩在 ai._call_api 上——打在某个提供商的客户端上会随默认模型变化而
静默失效。隔离数据库只打 database.core.DB_PATH 这个补丁。
"""
import json

import pytest
from unittest.mock import patch

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
import ai
import database
import importer
import main
import routes.story as story_routes

client = TestClient(main.app)

ENTRY_你好 = {"type": "vocabulary", "simplified": "你好", "pinyin": "nǐ hǎo",
               "english": "hello", "pos": "intj", "hsk": "1"}

# 16 个不同的词，供"词表平均切分"测试使用。
_MANY_WORDS = [
    "苹果", "香蕉", "橙子", "葡萄", "西瓜", "草莓", "桃子", "梨",
    "柠檬", "樱桃", "芒果", "菠萝", "石榴", "椰子", "木瓜", "柿子",
]


def write_yaml(tmp_path, name, entries):
    import yaml
    d = tmp_path / "Kouyu"
    d.mkdir(exist_ok=True)
    (d / name).write_text(yaml.dump({"entries": entries}, allow_unicode=True))


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(database.core, "DB_PATH", str(db_file))
    database.init_db()
    return db_file


@pytest.fixture
def populated_db(tmp_db, tmp_path):
    write_yaml(tmp_path, "words.yaml", [ENTRY_你好])
    importer.import_all(str(tmp_path))
    return next(d["id"] for d in database.get_all_decks() if d["name"] == "Kouyu")


@pytest.fixture
def many_words_db(tmp_db, tmp_path):
    entries = [
        {"type": "vocabulary", "simplified": w, "pinyin": w, "english": w,
         "pos": "n", "hsk": "1"}
        for w in _MANY_WORDS
    ]
    write_yaml(tmp_path, "words.yaml", entries)
    importer.import_all(str(tmp_path))
    return next(d["id"] for d in database.get_all_decks() if d["name"] == "Kouyu")


def _fake_podcast_sentences(cards, source, **kwargs):
    return [
        {"word_id": c["word_id"], "sentence_zh": f"{c['word_zh']}出现在这一集里。",
         "sentence_en": "", "target_word": c["word_zh"]}
        for c in cards
    ], f"假提示词：{source.get('title') if source else ''}"


# ── _parse_episode_ids（不变，仍是逗号串解析）───────────────────────────────

def test_parse_episode_ids_comma_separated():
    assert story_routes._parse_episode_ids("12,34,56", None) == [12, 34, 56]


def test_parse_episode_ids_falls_back_to_singular():
    assert story_routes._parse_episode_ids(None, 7) == [7]
    assert story_routes._parse_episode_ids("", 7) == [7]


def test_parse_episode_ids_empty_input_returns_empty_list():
    assert story_routes._parse_episode_ids(None, None) == []
    assert story_routes._parse_episode_ids("", None) == []


def test_parse_episode_ids_dedupes_and_preserves_order():
    assert story_routes._parse_episode_ids("5,3,5,7,3", None) == [5, 3, 7]


def test_parse_episode_ids_ignores_malformed_fragments():
    assert story_routes._parse_episode_ids("12,,abc, 34 ,", None) == [12, 34]


def test_parse_episode_ids_episode_ids_takes_priority_over_singular():
    # both given: the multi-select param wins, the legacy singular is only a
    # fallback for when episode_ids is absent.
    assert story_routes._parse_episode_ids("1,2", 999) == [1, 2]


# ── 词表切分 + 每次调用只带一个 source ──────────────────────────────────────

def _create_episode(vid, transcript, title, kind="podcast"):
    eid = database.create_pending_episode(
        vid, "https://example.com/feed.xml", title, None,
        f"https://example.com/{vid}", kind=kind)
    database.update_episode(eid, status="summarized", transcript_zh=transcript)
    return eid


def test_word_list_split_evenly_across_three_sources(many_words_db):
    """16 词 / 3 素材 → 6/5/5（余数分给靠前的素材）。"""
    deck_id = many_words_db
    ids = [
        _create_episode("m1", "甲" * 100, "素材甲"),
        _create_episode("m2", "乙" * 100, "素材乙"),
        _create_episode("m3", "丙" * 100, "素材丙"),
    ]

    calls = []

    def _capturing(cards, source, **kwargs):
        calls.append((source["title"], [c["word_zh"] for c in cards]))
        return _fake_podcast_sentences(cards, source, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": ",".join(str(i) for i in ids)})

    assert r.status_code == 200
    assert not r.json().get("error")
    assert mock_gen.call_count == 3   # one call per source, not one call total
    sizes = [len(words) for _, words in calls]
    assert sizes == [6, 5, 5]

    story = database.get_active_story(database.anki_today().isoformat(), "listening", deck_id)
    gen_params = json.loads(story["gen_params"])
    assert gen_params["episode_ids"] == ids
    assert gen_params["kind"] == "mixed"   # multiple sources → "mixed"


def test_each_call_gets_full_material_budget_not_divided(many_words_db):
    """#752 曾把预算按素材数量均分；#776 每次调用只带一个素材，不再共享
    上下文窗口，所以每份素材都拿到完整的 _KNOWLEDGE_MATERIAL_MAX_CHARS 预算。"""
    deck_id = many_words_db
    long_a = "甲" * (story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS + 500)
    long_b = "乙" * (story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS + 500)
    ids = [
        _create_episode("m1", long_a, "素材甲"),
        _create_episode("m2", long_b, "素材乙"),
    ]

    materials = []

    def _capturing(cards, source, **kwargs):
        materials.append(source["material"])
        return _fake_podcast_sentences(cards, source, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": ",".join(str(i) for i in ids)})

    assert r.status_code == 200
    assert not r.json().get("error")
    assert mock_gen.call_count == 2
    for m in materials:
        assert len(m) == story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS
    assert materials[0][0] == "甲"
    assert materials[1][0] == "乙"


def test_sentence_order_follows_source_order(many_words_db):
    """句子按素材顺序拼接：素材 1 的词全部排在素材 2 之前——这是本次改动
    存在的全部理由（Daniel 不要交替讲两份素材）。不依赖词表本身的原始顺序，
    直接记录每次 AI 调用实际收到的词，再核对最终句子顺序与之一致。"""
    deck_id = many_words_db
    ids = [
        _create_episode("m1", "第一份素材正文。", "素材一"),
        _create_episode("m2", "第二份素材正文。", "素材二"),
    ]

    calls = []

    def _capturing(cards, source, **kwargs):
        calls.append([c["word_zh"] for c in cards])
        return _fake_podcast_sentences(cards, source, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing):
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": ",".join(str(i) for i in ids)})

    assert r.status_code == 200
    body = r.json()
    assert not body.get("error")
    assert len(calls) == 2
    expected_order = calls[0] + calls[1]
    actual_order = [s["sentence_zh"].split("出现在这一集里。")[0] for s in body["sentences"]]
    assert actual_order == expected_order


def test_multi_source_skips_items_without_material(many_words_db):
    """选了 3 个素材，其中 1 个还没有转录/摘要——生成不因此整体失败，
    只是那一份被跳过，也就不会发起对应的 AI 调用。用多词的 fixture，
    确保每份有材料的素材都能分到至少一个词（避免因词数太少、词表被
    切分成空组而巧合地只调用一次）。"""
    deck_id = many_words_db
    eid_ok1 = database.create_pending_episode(
        "s1", "https://example.com/feed.xml", "有内容 1", None,
        "https://example.com/s1", kind="podcast")
    database.update_episode(eid_ok1, status="summarized", transcript_zh="正文一。")

    eid_empty = database.create_pending_episode(
        "s2", "https://example.com/feed.xml", "没内容", None,
        "https://example.com/s2", kind="podcast")
    database.update_episode(eid_empty, status="pending")  # 没有 transcript_zh/summary_de

    eid_ok2 = database.create_pending_episode(
        "s3", "https://example.com/feed.xml", "有内容 2", None,
        "https://example.com/s3", kind="video")
    database.update_episode(eid_ok2, status="summarized", transcript_zh="正文二。")

    calls = []

    def _capturing(cards, source, **kwargs):
        calls.append(source["title"])
        return _fake_podcast_sentences(cards, source, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": f"{eid_ok1},{eid_empty},{eid_ok2}"})

    assert r.status_code == 200
    assert not r.json().get("error")
    assert mock_gen.call_count == 2
    assert calls == ["有内容 1", "有内容 2"]


def test_knowledge_mode_requires_at_least_one_item(populated_db):
    deck_id = populated_db
    r = client.get(f"/api/story/{deck_id}/listening", params={"mode": "knowledge"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is True
    assert "at least one" in body["reason"]


def test_knowledge_mode_unknown_id_raises(populated_db):
    deck_id = populated_db
    r = client.get(f"/api/story/{deck_id}/listening",
                   params={"mode": "knowledge", "episode_ids": "999999"})
    assert r.status_code == 200
    body = r.json()
    assert body["error"] is True
    assert "999999" in body["reason"]


# ── 单素材路径：提示词里不能有任何 #752 遗留的多素材痕迹 ─────────────────────

CARDS = [
    {"word_id": 1, "word_zh": "承认", "pinyin": "chéngrèn", "definition": "admit"},
    {"word_id": 2, "word_zh": "顺便", "pinyin": "shùnbiàn", "definition": "by the way"},
]


def _reply(items):
    return json.dumps(items, ensure_ascii=False)


def test_single_source_prompt_has_no_multi_source_traces(monkeypatch):
    """提示词必须逐字回到 #752 之前的样子——Daniel 正在调这份提示词。"""
    sent = []

    def fake_call(model, messages, *a, **kw):
        sent.append(messages[0]["content"])
        return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。"},
                       {"sentence_zh": "他顺便去买了咖啡。"}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    source = {"title": "标题", "kind": "podcast", "url": None, "material": "Zusammenfassung"}
    ai.generate_podcast_sentences(CARDS, source)

    prompt = sent[0]
    assert "{multi_source_block}" not in prompt
    assert "多份素材" not in prompt
    assert "source_index" not in prompt
    assert "素材 2" not in prompt
    assert "素材甲" not in prompt
