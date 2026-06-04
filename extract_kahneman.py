"""
一次性脚本：从《思考，快与慢》中文版 PDF 提取每章"示例"句子，
保存为 data/kahneman_chapters.json。

运行方式：
    python extract_kahneman.py

需要环境变量：ANTHROPIC_API_KEY
"""

import base64
import json
import os
import sys
from pathlib import Path

import anthropic

_book_dir = Path(__file__).parent / "thinking fast and slow"
_candidates = list(_book_dir.glob("思考*快与慢*.pdf"))
PDF_PATH = _candidates[0] if _candidates else _book_dir / "思考.pdf"
OUTPUT_PATH = Path(__file__).parent / "data" / "kahneman_chapters.json"

PROMPT = """你正在阅读《思考，快与慢》（丹尼尔·卡尼曼著）的中文版PDF。

请提取书中每一章末尾的"示例"部分（有时标注为"关于……的例子"或类似标题）。
这些示例通常是带引号的短句，是某人在日常情境中说的话，暗示某种认知偏误或心理现象正在发挥作用。

对于书中每一章，请提取：
1. 章节编号（数字）
2. 章节标题（中文）
3. 对应英文标题（如果你知道的话）
4. 该章核心概念的一句话说明（中文，20-40字）
5. 对应英文概念说明（一句话，英文）
6. 该章末尾"示例"部分的所有引用句（中文，保留引号）

请以如下 JSON 格式输出，不要添加任何其他文字：

{
  "chapters": [
    {
      "number": 1,
      "title_zh": "本书的主角",
      "title_en": "The Characters of the Story",
      "concept_zh": "系统1（快速、直觉、自动）与系统2（缓慢、理性、费力）是大脑思考的两套机制",
      "concept_en": "System 1 (fast, intuitive, automatic) and System 2 (slow, deliberate, effortful) are the two systems of the mind",
      "examples_zh": [
        "\"他有印象，只是其中一部分是幻象。\"",
        "\"这纯粹是系统1的反应，她在意识到危险之前就果断采取了行动。\"",
        "\"这是你系统1的想法，放慢速度，听听系统2的看法吧。\""
      ]
    }
  ]
}

注意：
- 只提取"示例"部分的引用句，不要包含章节正文内容
- 如果某章没有"示例"部分，跳过该章
- 引用句保留原文标点和引号
- 确保 JSON 格式合法，可以直接解析"""


def main():
    if not PDF_PATH.exists():
        print(f"错误：找不到 PDF 文件：{PDF_PATH}", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("错误：缺少 ANTHROPIC_API_KEY 环境变量", file=sys.stderr)
        sys.exit(1)

    print(f"读取 PDF：{PDF_PATH.name}")
    with open(PDF_PATH, "rb") as f:
        pdf_data = base64.standard_b64encode(f.read()).decode("utf-8")
    print(f"PDF 大小：{len(pdf_data) * 3 // 4 / 1024 / 1024:.1f} MB（base64编码后）")

    client = anthropic.Anthropic(api_key=api_key)

    print("发送到 Claude API 提取章节数据...")
    response = client.messages.create(
        model="claude-opus-4-8",
        max_tokens=32000,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": pdf_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": PROMPT,
                    },
                ],
            }
        ],
    )

    raw = response.content[0].text.strip()
    print(f"API 响应：{response.usage.input_tokens} 输入词元，{response.usage.output_tokens} 输出词元")

    # 提取 JSON（有时模型会在 JSON 前后加说明文字）
    json_start = raw.find("{")
    json_end = raw.rfind("}") + 1
    if json_start == -1 or json_end == 0:
        print("错误：响应中找不到 JSON 数据", file=sys.stderr)
        print("原始响应：", raw[:500])
        sys.exit(1)

    json_str = raw[json_start:json_end]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"错误：JSON 解析失败：{e}", file=sys.stderr)
        print("原始响应前500字：", raw[:500])
        sys.exit(1)

    chapters = data.get("chapters", [])
    print(f"成功提取 {len(chapters)} 个章节")
    for ch in chapters:
        n_examples = len(ch.get("examples_zh", []))
        print(f"  第{ch.get('number', '?')}章：{ch.get('title_zh', '?')}（{n_examples} 条示例）")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n已保存到：{OUTPUT_PATH}")


if __name__ == "__main__":
    main()
