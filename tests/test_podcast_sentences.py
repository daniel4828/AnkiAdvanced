"""ai.generate_podcast_sentences（#561，提示词于 #634 重写）。

AI 一律打桩在 ai._call_api 上——打在某个提供商的客户端上会随默认模型变化而静默失效。
"""
import json

import pytest

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
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert [s["reasoning_zh"] for s in sentences] == [
        "话题：字节跳动的AI路线。", "话题：抖音电商。"]
    assert [s["word_ids"] for s in sentences] == [[1], [2]]


def test_missing_reasoning_is_not_fatal(monkeypatch):
    """模型漏掉 reasoning_zh 时只是少一段背景说明，句子本身照常入库。"""
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"sentence_zh": "张一鸣承认公司暂时落后。"},
        {"sentence_zh": "他在抖音上刷视频，顺便就下了单。"},
    ]))
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert [s["reasoning_zh"] for s in sentences] == ["", ""]
    assert len(sentences) == 2


def test_skipped_word_is_re_requested(monkeypatch):
    """#642：模型漏词时继续补漏，而不是 3 轮后丢给兜底句。
    补漏轮的提示词必须点名漏掉的词——原来重发的是一模一样的提示词。"""
    prompts = []

    def fake_call(model, messages, *a, **kw):
        prompts.append(messages[0]["content"])
        if len(prompts) == 1:                      # 第一轮只写了一个词
            return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。"}])
        return _reply([{"sentence_zh": "他在抖音上刷视频，顺便就下了单。"}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert len(prompts) == 2
    assert "顺便" in prompts[1] and "补漏轮" in prompts[1]
    assert sorted(s["word_ids"][0] for s in sentences) == [1, 2]
    assert not any("我学了" in s["sentence_zh"] for s in sentences)


def test_fallback_only_after_all_rounds(monkeypatch):
    """AI 始终不写句子时仍然收敛：每张卡都有句子，且轮数有上限。"""
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([]))
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert len(sentences) == 2
    assert all("我学了" in s["sentence_zh"] for s in sentences)


def test_progress_log_is_recorded(monkeypatch):
    """#642：加载界面的日志按 progress_key 累积，供 /api/story-progress 返回。"""
    monkeypatch.setattr(ai, "_call_api", lambda *a, **kw: _reply([
        {"sentence_zh": "张一鸣承认公司暂时落后。"},
        {"sentence_zh": "他在抖音上刷视频，顺便就下了单。"},
    ]))
    key = "test/reading/zh"
    ai.reset_story_log(key)
    ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题", progress_key=key)

    log = ai._story_log[key]
    assert any("开始生成播客句子" in line for line in log)
    assert any("还差 0 个词" in line for line in log)
    ai.reset_story_log(key)


# ── #697: 返回实际发出去的提示词，供加星句子回看 ──────────────────────────────

def test_returns_the_prompt_actually_sent(monkeypatch):
    """加星句子要能翻到生成它的提示词，所以这里必须把提示词交出来——
    而且是真发出去的那份，不是事后重建的。"""
    sent = []

    def fake_call(model, messages, *a, **kw):
        sent.append(messages[0]["content"])
        return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。"},
                       {"sentence_zh": "他在抖音上刷视频，顺便就下了单。"}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "字节跳动的一集", "标题")

    assert prompt == sent[0]
    assert "字节跳动的一集" in prompt      # 素材
    assert "承认" in prompt and "顺便" in prompt   # 目标词


def test_prompt_includes_every_retry_round(monkeypatch):
    """补漏轮带 extra_hint，提示词和第一轮不同——只留第一轮就说不清
    这些句子到底是被什么提示词写出来的。"""
    def fake_call(model, messages, *a, **kw):
        if "补漏轮" not in messages[0]["content"]:
            return _reply([{"sentence_zh": "张一鸣承认公司暂时落后。"}])
        return _reply([{"sentence_zh": "他在抖音上刷视频，顺便就下了单。"}])

    monkeypatch.setattr(ai, "_call_api", fake_call)
    _, prompt = ai.generate_podcast_sentences(CARDS, "Zusammenfassung", "标题")

    assert "补漏轮" in prompt
    assert prompt.count("【补漏轮】") == 1


def test_no_material_returns_empty_prompt(monkeypatch):
    """没有素材时不调 AI，也就没有提示词可存。"""
    monkeypatch.setattr(ai, "_call_api",
                        lambda *a, **kw: pytest.fail("素材为空时不该调 AI"))
    sentences, prompt = ai.generate_podcast_sentences(CARDS, "   ", "标题")
    assert sentences == [] and prompt == ""
