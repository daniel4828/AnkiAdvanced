"""translator.translate_batch 的分块与进度上报（#756/#758）。

回归背景：原来整批拼成一个字符串发出，长故事超过 Google 的 5000 字上限就
报错，回退成"每句一次请求"——228 句串行跑几分钟，而进度只在首尾更新，
界面永远显示 Translating… 0/228，看着像卡死。
"""
import translator


class FakeTranslator:
    """记录每次请求的输入；翻译 = 每行加前缀。"""

    def __init__(self):
        self.calls: list[str] = []

    def translate(self, text: str) -> str:
        self.calls.append(text)
        if len(text) > 5000:
            raise ValueError("Text length need to be between 0 and 5000 characters")
        return "\n".join("de:" + line for line in text.split("\n"))


def _patch(monkeypatch, fake):
    monkeypatch.setattr(translator, "_load", lambda source, target: fake)


def test_long_batch_is_split_into_several_requests(monkeypatch):
    fake = FakeTranslator()
    _patch(monkeypatch, fake)
    texts = [f"第{i}句话，内容足够长以便凑够字符数。" for i in range(228)]

    out = translator.translate_batch(texts, target="de")

    assert out == ["de:" + t for t in texts]
    assert 1 < len(fake.calls) < len(texts), "应分成几块，而不是一次或每句一次"
    assert all(len(c) <= 5000 for c in fake.calls)


def test_short_batch_stays_one_request(monkeypatch):
    fake = FakeTranslator()
    _patch(monkeypatch, fake)

    out = translator.translate_batch(["你好", "再见"], target="de")

    assert out == ["de:你好", "de:再见"]
    assert len(fake.calls) == 1


def test_progress_is_reported_per_chunk(monkeypatch):
    """进度必须在中途上报，不能从 0/N 直接跳到 N/N——那正是 #756 的症状。"""
    fake = FakeTranslator()
    _patch(monkeypatch, fake)
    monkeypatch.setattr(translator, "_CHUNK_CHAR_BUDGET", 10)
    seen: list[tuple[int, int]] = []

    texts = ["句一", "句二", "句三", "句四"]
    translator.translate_batch(texts, target="de",
                               on_progress=lambda d, t: seen.append((d, t)))

    assert seen, "必须至少上报一次"
    assert all(t == len(texts) for _, t in seen)
    assert [d for d, _ in seen] == sorted(d for d, _ in seen), "done 必须单调不减"
    assert seen[-1][0] == len(texts)
    assert len(seen) > 1, "中途要有进度，不能只报最后一次"


def test_failing_chunk_falls_back_only_for_itself(monkeypatch):
    """一块失败时只有该块逐句重试，其它块的结果照常保留。"""
    fake = FakeTranslator()

    def flaky(text: str) -> str:
        if "坏句" in text and "\n" in text:
            raise RuntimeError("boom")
        return "\n".join("de:" + line for line in text.split("\n"))

    fake.translate = flaky  # type: ignore[method-assign]
    _patch(monkeypatch, fake)
    monkeypatch.setattr(translator, "_CHUNK_CHAR_BUDGET", 10)

    out = translator.translate_batch(["好句一", "好句二", "坏句", "坏句尾"], target="de")

    assert out == ["de:好句一", "de:好句二", "de:坏句", "de:坏句尾"]


def test_over_long_single_text_still_returned(monkeypatch):
    """单句就超限时不能被丢掉——退回原文即可。"""
    fake = FakeTranslator()
    _patch(monkeypatch, fake)
    huge = "字" * 6000

    out = translator.translate_batch([huge, "短句"], target="de")

    assert len(out) == 2
    assert out[0] == huge  # 翻译失败 → 原样返回
    assert out[1] == "de:短句"
