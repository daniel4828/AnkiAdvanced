"""知识库故事模式测试（issue #654）：podcast 模式的故事生成 → knowledge 模式。

核心保证：
- mode='knowledge' 的新故事能按视频/文章素材正常生成（走既有的
  ai.generate_podcast_sentences 管线，episode_id 指向任意 kind）。
- 新故事生成拒绝旧标识符 mode='podcast'（照 #512 移除旧 news 模式的做法）。
- mode='podcast' 的历史故事仍能通过 GET /api/story 正常展示（读取路径不拒绝），
  且 Again 单句重生成（generate_sentence_for_word）仍能工作。
"""
import pytest
from unittest.mock import patch

pytest.importorskip("fastapi", reason="fastapi not installed")

from fastapi.testclient import TestClient
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


def _fake_podcast_sentences(cards, summary, title, **kwargs):
    return [
        {"word_id": c["word_id"], "sentence_zh": f"{c['word_zh']}出现在这一集里。",
         "sentence_en": "", "target_word": c["word_zh"]}
        for c in cards
    ]


def test_knowledge_mode_generates_story_from_video_episode(populated_db):
    """kind='video' 的素材走同一条 knowledge 生成管线（阶段 A 之后 get_episode
    对三种 kind 一视同仁）。"""
    deck_id = populated_db
    episode_id = database.create_pending_episode(
        "yt123", "https://youtube.com/@x", "一个视频标题", None,
        "https://youtube.com/watch?v=yt123", kind="video")
    database.update_episode(episode_id, status="summarized",
                            summary_de="Ein deutsches Resümee.", summary_zh="中文摘要")

    with patch("ai.generate_podcast_sentences", side_effect=_fake_podcast_sentences) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge", "episode_id": episode_id})

    assert r.status_code == 200
    body = r.json()
    assert body is not None and not body.get("error")
    assert len(body["sentences"]) == 1
    mock_gen.assert_called_once()

    story = database.get_active_story(database.anki_today().isoformat(), "listening", deck_id)
    gen_params = __import__("json").loads(story["gen_params"])
    assert gen_params["mode"] == "knowledge"
    assert gen_params["episode_id"] == episode_id
    assert gen_params["kind"] == "video"  # #654: kind recorded alongside episode_id


def test_new_story_rejects_old_podcast_mode_identifier(populated_db):
    """#654（照 #512 先例）：新故事生成拒绝旧标识符 mode='podcast'，只接受
    'knowledge'。"""
    deck_id = populated_db
    episode_id = database.create_pending_episode(
        "pc123", "https://example.com/feed.xml", "一期播客", None,
        "https://example.com/ep1", kind="podcast")
    database.update_episode(episode_id, status="summarized", summary_de="Zusammenfassung.")

    r = client.get(f"/api/story/{deck_id}/listening",
                   params={"mode": "podcast", "episode_id": episode_id})
    assert r.status_code == 200  # error is returned as a JSON error dict, not an HTTP error
    body = r.json()
    assert body["error"] is True
    assert "podcast" in body["reason"] and "knowledge" in body["reason"]


def test_historical_podcast_story_still_displays(populated_db):
    """mode='podcast' 的历史故事必须继续能被 GET /api/story 正常读取展示——
    读取路径不做任何拒绝，只有新生成才拒绝旧标识符。"""
    deck_id = populated_db
    card = story_routes._get_cards_for_story(deck_id, "listening")[0]
    today = database.anki_today().isoformat()
    database.create_story(
        today, "listening", deck_id,
        [{"position": 0, "sentence_zh": "你好出现在这一集里。", "sentence_en": "",
          "word_ids": [card["word_id"]]}],
        prompt_text="podcast mode — episode 1",
        gen_params={"mode": "podcast", "episode_id": 1, "max_hsk": 3, "model": None,
                   "topic": None, "grammar_focus": None, "grammar_pct": 75,
                   "chapter_ids": None, "articles": None, "lang": "zh",
                   "origin": None, "batch_size": None},
        lang="zh")

    r = client.get(f"/api/story/{deck_id}/listening")
    assert r.status_code == 200
    body = r.json()
    assert body is not None
    assert len(body["sentences"]) == 1
    assert body["sentences"][0]["sentence_zh"] == "你好出现在这一集里。"


def test_historical_podcast_story_again_regen_still_works(populated_db):
    """Again 单句重生成（generate_sentence_for_word）对历史 mode='podcast'
    故事仍要工作，走 ai.generate_podcast_sentences 同一条管线。"""
    deck_id = populated_db
    card = story_routes._get_cards_for_story(deck_id, "listening")[0]
    episode_id = database.create_pending_episode(
        "pc999", "https://example.com/feed.xml", "旧单集", None,
        "https://example.com/ep-old", kind="podcast")
    database.update_episode(episode_id, status="summarized", summary_de="Alte Zusammenfassung.")

    gen_params = {"mode": "podcast", "episode_id": episode_id, "max_hsk": 3, "model": None}
    with patch("ai.generate_podcast_sentences", side_effect=_fake_podcast_sentences) as mock_gen:
        result = story_routes.generate_sentence_for_word(card, gen_params)

    assert result is not None
    assert result["sentence_zh"]
    mock_gen.assert_called_once()


# ── issue #661: knowledge mode material is transcript_zh, summary_de is only
# a fallback for rows without a transcript (#561 had switched to summary-only
# purely to cut cost/latency; that stopped mattering once cost was confirmed
# to be ~$0.003/generation — see routes/story.py's _knowledge_material()). ──

def test_knowledge_material_prefers_transcript():
    """有 transcript_zh 时用它（截断到 15000 字），不是 summary_de。"""
    ep = {"transcript_zh": "这是完整的转录正文。" * 5, "summary_de": "Eine Zusammenfassung."}
    material = story_routes._knowledge_material(ep)
    assert material == ep["transcript_zh"]
    assert "Zusammenfassung" not in material


def test_knowledge_material_truncates_long_transcript():
    """转录截断到 _KNOWLEDGE_MATERIAL_MAX_CHARS（issue #661，与 knowledge/article.py
    的 _MAX_ARTICLE_CHARS 一致），是上下文窗口护栏，不是省钱考量。"""
    long_transcript = "字" * (story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS + 500)
    ep = {"transcript_zh": long_transcript, "summary_de": ""}
    material = story_routes._knowledge_material(ep)
    assert len(material) == story_routes._KNOWLEDGE_MATERIAL_MAX_CHARS


def test_knowledge_material_falls_back_to_summary_without_error():
    """没有 transcript_zh 的旧行（例如只同步下来摘要）自动退回 summary_de，不报错。"""
    ep = {"transcript_zh": "", "summary_de": "Nur eine Zusammenfassung."}
    assert story_routes._knowledge_material(ep) == "Nur eine Zusammenfassung."

    ep_missing_key = {"summary_de": "Nur eine Zusammenfassung."}
    assert story_routes._knowledge_material(ep_missing_key) == "Nur eine Zusammenfassung."

    assert story_routes._knowledge_material({}) == ""


def test_knowledge_mode_story_generation_uses_transcript(populated_db):
    """新故事生成走 transcript_zh，而不是 summary_de（issue #661）。"""
    deck_id = populated_db
    episode_id = database.create_pending_episode(
        "yt661", "https://youtube.com/@x", "转录素材视频", None,
        "https://youtube.com/watch?v=yt661", kind="video")
    database.update_episode(
        episode_id, status="summarized",
        transcript_zh="转录正文提到了你好这个词的使用场景。",
        summary_de="Eine kurze deutsche Zusammenfassung.")

    seen_material = {}

    def _capturing(cards, material, title, **kwargs):
        seen_material["value"] = material
        return _fake_podcast_sentences(cards, material, title, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge", "episode_id": episode_id})

    assert r.status_code == 200
    assert not r.json().get("error")
    mock_gen.assert_called_once()
    assert seen_material["value"] == "转录正文提到了你好这个词的使用场景。"
    assert "Zusammenfassung" not in seen_material["value"]


def test_knowledge_mode_story_generation_falls_back_without_transcript(populated_db):
    """旧行只有 summary_de 时，新故事生成仍然成功（自动降级，不报错）。"""
    deck_id = populated_db
    episode_id = database.create_pending_episode(
        "yt661b", "https://youtube.com/@x", "只有摘要的旧视频", None,
        "https://youtube.com/watch?v=yt661b", kind="video")
    database.update_episode(episode_id, status="summarized",
                            summary_de="Nur eine deutsche Zusammenfassung.")

    seen_material = {}

    def _capturing(cards, material, title, **kwargs):
        seen_material["value"] = material
        return _fake_podcast_sentences(cards, material, title, **kwargs)

    with patch("ai.generate_podcast_sentences", side_effect=_capturing) as mock_gen:
        r = client.get(f"/api/story/{deck_id}/listening",
                       params={"mode": "knowledge", "episode_id": episode_id})

    assert r.status_code == 200
    assert not r.json().get("error")
    mock_gen.assert_called_once()
    assert seen_material["value"] == "Nur eine deutsche Zusammenfassung."
