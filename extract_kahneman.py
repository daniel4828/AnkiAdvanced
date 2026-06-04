"""
Extract chapter example sentences from the Chinese PDF of Thinking, Fast and Slow.
Saves output to data/kahneman_chapters.json.

Usage:
    python extract_kahneman.py
"""

import json
import re
from pathlib import Path

import pdfplumber

_book_dir = Path(__file__).parent / "thinking fast and slow"
_candidates = list(_book_dir.glob("思考*.pdf"))
PDF_PATH = _candidates[0] if _candidates else _book_dir / "thinking.pdf"
META_PATH = Path(__file__).parent / "data" / "kahneman_meta.json"
OUTPUT_PATH = Path(__file__).parent / "data" / "kahneman_chapters.json"

OPEN_QUOTE = "“"
CLOSE_QUOTE = "”"


def extract_quoted_sentences(text):
    text = re.sub("读累了.*?学习资源分享", "", text, flags=re.DOTALL)
    quotes = re.findall(OPEN_QUOTE + r"(.+?)" + CLOSE_QUOTE, text, re.DOTALL)
    results = []
    for q in quotes:
        q = re.sub(r"\s+", "", q).strip()
        if len(q) > 5:
            results.append(OPEN_QUOTE + q + CLOSE_QUOTE)
    return results


def main():
    print(f"Reading PDF: {PDF_PATH.name}")
    all_text = []
    with pdfplumber.open(str(PDF_PATH)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            all_text.append(t or "")
    full = "\n".join(all_text)

    with open(META_PATH, encoding="utf-8") as f:
        chapters_meta = json.load(f)

    marker = "示例"
    positions = [
        m.start() for m in re.finditer(marker, full)
        if m.start() < 300000 and not (125000 <= m.start() <= 126500)
    ]
    print(f"Found {len(positions)} chapter example blocks")
    if len(positions) != 38:
        print(f"WARNING: expected 38, got {len(positions)}")

    chapters = []
    for i, meta in enumerate(chapters_meta):
        pos = positions[i]
        section_text = full[pos: pos + 800]
        examples = extract_quoted_sentences(section_text)
        entry = dict(meta)
        entry["examples_zh"] = examples
        chapters.append(entry)
        print(f"  Ch{meta['number']:2d} {meta['title_zh'][:16]} -> {len(examples)} examples")

    data = {"chapters": chapters}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    total = sum(len(c["examples_zh"]) for c in chapters)
    print(f"\nSaved to {OUTPUT_PATH} ({total} example sentences total)")


if __name__ == "__main__":
    main()
