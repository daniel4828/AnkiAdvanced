"""知识库故事模式的多素材生成（issue #752）：一次生成混合多个知识库素材
（播客/视频/文章），到期词分散到几份材料上。

覆盖：
- routes.story._parse_episode_ids 的输入解析
- 多素材时材料预算按素材数量均分，且 ai.generate_podcast_sentences 收到的
  sources[] 结构正确（title/kind/url/material/index）
- ai.generate_podcast_sentences 按模型返回的 source_index 回填每句的
  source_url/source_title，越界/缺失时回退到第一个素材
- 单素材路径下 {multi_source_block} 渲染为空，提示词与旧版逐字一致

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


def _fake_podcast_sentences(cards, sources, **kwargs):
    return [
        {"word_id": c["word_id"], "sentence_zh": f"{c['word_zh']}出现在这一集里。",
         "sentence_en": "", "target_word": c["word_zh"]}
        for c in cards
    ], "假提示词"


# ── _parse_episode_ids ───────────────────────────────────────────────────────

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


# ── material budget split + sources[] structure ─────────────────────────────

def test_multi_source_budget_split_evenly(populated_db):
    """三个素材同时选中时，每份材料的预算是总预算的三分之一——不是三份各自
    拿到全额（那会让总提示词随选择数量线性膨胀，撑爆上下文窗口）。"""
    deck_id = populated_db
    long_transcript_a = "甲" * 100
    long_transcript_b = "乙" * 100
    long_transcript_c = "丙" * 100
    ids = []
    for vid, transcript, title in [
        ("m1", long_transcript_a, "素材甲"),
        ("m2", long_transcript_b, "素材乙"),
        ("m3", long_transcript_c, "素材丙"),
    ]:
        eid = database.create_pending_episode(
            vid, "https://example.com/feed.xml", title, None,
            f"https://example.com/{vid}", kind="podcast")
        database.update_episode(eid, status="summarized", transcript_zh=transcript)
        ids.append(eid)

    captured = {}

    def _capturing(cards, sources, **kwargs):
        captured["sources"] = sources
        return _fake_podcast_sentences(cards, sources, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": ",".join(str(i) for i in ids)})

    assert r.status_code == 200
    assert not r.json().get("error")
    mock_gen.assert_called_once()

    sources = captured["sources"]
    assert len(sources) == 3
    expected_limit = story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS // 3
    for s in sources:
        assert len(s["material"]) <= expected_limit
    # every material distinct — budget applied per-source, not shared/truncated
    # to a single common slice
    assert sources[0]["material"][0] == "甲"
    assert sources[1]["material"][0] == "乙"
    assert sources[2]["material"][0] == "丙"
    # index is 1-based and matches submission order
    assert [s["index"] for s in sources] == [1, 2, 3]
    assert [s["title"] for s in sources] == ["素材甲", "素材乙", "素材丙"]
    assert all(s["kind"] == "podcast" for s in sources)

    story = database.get_active_story(database.anki_today().isoformat(), "listening", deck_id)
    gen_params = json.loads(story["gen_params"])
    assert gen_params["episode_ids"] == ids
    assert gen_params["episode_id"] == ids[0]   # legacy singular key = first id
    assert gen_params["kind"] == "mixed"        # #752: multiple sources → "mixed"


def test_multi_source_skips_items_without_material(populated_db):
    """选了 3 个素材，其中 1 个还没有转录/摘要——生成不因此整体失败，
    只是那一份被跳过（routes/story.py 的 knowledge 分支约定，见施工图）。"""
    deck_id = populated_db
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

    captured = {}

    def _capturing(cards, sources, **kwargs):
        captured["sources"] = sources
        return _fake_podcast_sentences(cards, sources, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge",
                               "episode_ids": f"{eid_ok1},{eid_empty},{eid_ok2}"})

    assert r.status_code == 200
    assert not r.json().get("error")
    mock_gen.assert_called_once()
    sources = captured["sources"]
    assert len(sources) == 2
    assert [s["title"] for s in sources] == ["有内容 1", "有内容 2"]


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


# ── ai.generate_podcast_sentences: source_index attribution ────────────────

CARDS = [
    {"word_id": 1, "word_zh": "承认", "pinyin": "chéngrèn", "definition": "admit"},
    {"word_id": 2, "word_zh": "顺便", "pinyin": "shùnbiàn", "definition": "by the way"},
]

SOURCES_2 = [
    {"index": 1, "title": "素材一", "kind": "podcast", "url": "https://a.example/1", "material": "第一份素材正文。"},
    {"index": 2, "title": "素材二", "kind": "video", "url": "https://b.example/2", "material": "第二份素材正文。"},
]


def _reply(items):
    return json.dumps(items, ensure_ascii=False)


def test_source_index_attributes_sentence_to_right_source(monkeypatch):
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"sentence_zh": "张一鸣承认公司暂时落后。", "source_index": 2},
        {"sentence_zh": "他顺便去买了咖啡。", "source_index": 1},
    ]))
    sentences, _ = ai.generate_podcast_sentences(CARDS, SOURCES_2)

    by_word = {s["word_ids"][0]: s for s in sentences}
    assert by_word[1]["source_url"] == "https://b.example/2"
    assert by_word[1]["source_title"] == "素材二"
    assert by_word[2]["source_url"] == "https://a.example/1"
    assert by_word[2]["source_title"] == "素材一"


def test_source_index_out_of_range_falls_back_to_first_source(monkeypatch):
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"sentence_zh": "张一鸣承认公司暂时落后。", "source_index": 99},
        {"sentence_zh": "他顺便去买了咖啡。"},   # missing entirely
    ]))
    sentences, _ = ai.generate_podcast_sentences(CARDS, SOURCES_2)

    for s in sentences:
        assert s["source_url"] == "https://a.example/1"
        assert s["source_title"] == "素材一"


def test_multi_source_prompt_lists_sources_with_numbered_titles(monkeypatch):
    sent = []

    def fake_call(model, messages, *a, **kw):
        sent.append(messages[0]["content"])
        return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。", "source_index": 1},
                       {"sentence_zh": "他顺便去买了咖啡。", "source_index": 2}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    ai.generate_podcast_sentences(CARDS, SOURCES_2)

    prompt = sent[0]
    assert "1. 《素材一》（podcast）" in prompt
    assert "2. 《素材二》（video）" in prompt
    assert "第一份素材正文。" in prompt
    assert "第二份素材正文。" in prompt
    assert "source_index" in prompt   # multi_source_block instructs the model


# ── single-source path: {multi_source_block} renders empty (逐字不变) ───────

def test_single_source_prompt_has_no_multi_source_block(monkeypatch):
    """单素材时提示词里不能出现 #752 新增的多素材说明，否则说明单素材路径
    被这次改动动过了——Daniel 正在调这份提示词，单素材必须逐字不变。"""
    sent = []

    def fake_call(model, messages, *a, **kw):
        sent.append(messages[0]["content"])
        return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。"},
                       {"sentence_zh": "他顺便去买了咖啡。"}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    single_source = [{"index": 1, "title": "标题", "kind": "podcast",
                       "url": None, "material": "Zusammenfassung"}]
    ai.generate_podcast_sentences(CARDS, single_source)

    prompt = sent[0]
    assert "{multi_source_block}" not in prompt
    assert "多份素材" not in prompt
    assert "source_index" not in prompt
