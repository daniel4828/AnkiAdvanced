"""ai.generate_podcast_sentences（#561，提示词于 #634 重写）。

AI 一律打桩在 ai._call_api 上——打在某个提供商的客户端上会随默认模型变化而静默失效。
"""
import json

import ai


CARDS = [
    {"word_id": 1, "word_zh": "承认", "pinyin": "chéngrèn", "definition": "admit"},
    {"word_id": 2, "word_zh": "顺便", "pinyin": "shùnbiàn", "definition": "by the way"},
]


def _reply(items):
    return json.dumps(items, ensure_ascii=False)


def test_reasoning_is_kept(monkeypatch):
    """#634：模型先想话题/锚点/档位再写句子，这段推理要存进 reasoning_zh
    （背景弹窗里显示），以前被硬编码成空字符串。"""
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"reasoning_zh": "话题：字节跳动的AI路线。", "sentence_zh": "张一鸣承认公司暂时落后。",
         "target_word": "承认"},
        {"reasoning_zh": "话题：抖音电商。", "sentence_zh": "他在抖音上刷视频，顺便就下了单。",
         "target_word": "顺便"},
    ]))
    sentences = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert [s["reasoning_zh"] for s in sentences] == [
        "话题：字节跳动的AI路线。", "话题：抖音电商。"]
    assert [s["word_ids"] for s in sentences] == [[1], [2]]


def test_missing_reasoning_is_not_fatal(monkeypatch):
    """模型漏掉 reasoning_zh 时只是少一段背景说明，句子本身照常入库。"""
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"sentence_zh": "张一鸣承认公司暂时落后。"},
        {"sentence_zh": "他在抖音上刷视频，顺便就下了单。"},
    ]))
    sentences = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert [s["reasoning_zh"] for s in sentences] == ["", ""]
    assert len(sentences) == 2
