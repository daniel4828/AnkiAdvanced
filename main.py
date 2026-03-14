"""FastAPI app + CLI entry point."""
import json
import sys
import threading
from datetime import date
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai
import database
import importer
import srs
import tts

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")


# ── Serve frontend ─────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return FileResponse("static/index.html")


# ── API ────────────────────────────────────────────────────────────────────────

def _card_with_intervals(c):
    d = dict(c)
    d["intervals"] = srs.preview_intervals(
        c["state"], c["interval"], c["ease"], c["learning_step"] or 0
    )
    return d


@app.get("/api/today")
def get_today():
    today = date.today().isoformat()
    result = {}
    for cat in ("listening", "reading", "creating"):
        cards = database.get_due_cards(cat, today)
        result[cat] = [_card_with_intervals(c) for c in cards]
    return result


@app.get("/api/learning_due/{category}")
def get_learning_due(category: str):
    """Learning cards whose minute-timer has now expired — prepend these to the queue."""
    cards = database.get_due_learning_cards(category)
    return [_card_with_intervals(c) for c in cards]


@app.get("/api/content/{day}/{category}")
def get_content(day: str, category: str):
    """Return cached daily content, generating it if needed."""
    row = database.get_daily_content(day, category)
    if row:
        return dict(row)

    # Generate
    cards = database.get_due_cards(category, day)
    if not cards:
        raise HTTPException(status_code=404, detail="No due cards")

    target_words = [
        {"word_zh": c["word_zh"], "pinyin": c["pinyin"], "definition": c["definition"]}
        for c in cards
    ]
    word_ids = [c["word_id"] for c in cards]

    generated = ai.generate_sentences(target_words)
    sentences_zh = generated.get("sentences_zh", [])
    sentences_en = generated.get("sentences_en", [])

    # Pad or trim to match word count
    n = len(word_ids)
    sentences_zh = (sentences_zh + [w["word_zh"] for w in target_words])[:n]
    sentences_en = (sentences_en + [w["definition"] for w in target_words])[:n]

    database.save_daily_content(
        date=day,
        category=category,
        word_ids=json.dumps(word_ids),
        sentences_zh=json.dumps(sentences_zh, ensure_ascii=False),
        sentences_en=json.dumps(sentences_en, ensure_ascii=False),
    )

    return dict(database.get_daily_content(day, category))


class ReviewRequest(BaseModel):
    card_id: int
    rating: int
    user_response: str | None = None


@app.post("/api/review")
def post_review(body: ReviewRequest):
    card = database.get_card(body.card_id)
    if not card:
        raise HTTPException(status_code=404, detail="Card not found")
    if body.rating not in (1, 2, 3, 4):
        raise HTTPException(status_code=400, detail="Rating must be 1-4")

    new_rep, new_interval, new_ease, new_state, new_step, learning_due = srs.next_review(
        card["repetitions"], card["interval"], card["ease"], body.rating,
        card["state"], card["learning_step"] or 0,
    )
    new_due = srs.due_date(new_interval)

    database.update_card(body.card_id, new_state, new_due, new_interval, new_ease,
                         new_rep, new_step, learning_due)
    # Only push sibling due dates when graduating to review (not while in learning steps)
    if new_state == "review":
        database.push_sibling_due_dates(card["word_id"], card["category"], new_due)
    database.unlock_creating_card(card["word_id"])
    database.log_review(body.card_id, body.rating, body.user_response)

    return {"next_due": new_due, "interval": new_interval}


class SpeakRequest(BaseModel):
    text: str
    rate: int = 175


@app.post("/api/speak")
def post_speak(body: SpeakRequest):
    """Trigger TTS in a background thread so the response returns immediately."""
    threading.Thread(target=tts.speak, args=(body.text, body.rate), daemon=True).start()
    return {"ok": True}


@app.get("/api/word/{word_id}")
def get_word(word_id: int):
    detail = database.get_word_detail(word_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Word not found")
    return detail


@app.get("/queue")
def queue_debug():
    from fastapi.responses import HTMLResponse
    today = date.today().isoformat()
    now_str = __import__("datetime").datetime.now().strftime("%H:%M:%S")

    rows = []
    with database.get_conn() as conn:
        cards = conn.execute("""
            SELECT c.id, c.category, c.state, c.due_date, c.interval, c.ease,
                   c.repetitions, c.learning_step, c.learning_due,
                   w.word_zh, w.pinyin
            FROM cards c JOIN words w ON w.id = c.word_id
            WHERE c.state != 'locked'
            ORDER BY c.category,
              CASE c.state WHEN 'learning' THEN 0 WHEN 'review' THEN 1 ELSE 2 END,
              COALESCE(c.learning_due, c.due_date)
        """).fetchall()

    for c in cards:
        state_color = {"new": "#4aff88", "learning": "#ffaa4a", "review": "#4a9eff"}.get(c["state"], "#888")
        due = c["learning_due"] or c["due_date"] or ""
        rows.append(f"""<tr>
            <td>{c['word_zh']}</td>
            <td style='color:#888'>{c['pinyin']}</td>
            <td>{c['category']}</td>
            <td style='color:{state_color};font-weight:600'>{c['state']}</td>
            <td>step {c['learning_step'] or 0}</td>
            <td>{due}</td>
            <td>{c['interval']}d</td>
            <td>{c['repetitions']}</td>
            <td>{c['ease']:.2f}</td>
        </tr>""")

    html = f"""<!DOCTYPE html><html><head>
    <meta charset='UTF-8'>
    <meta http-equiv='refresh' content='10'>
    <title>Queue</title>
    <style>
      body{{background:#0f0f0f;color:#e8e8e8;font-family:monospace;padding:20px;font-size:13px}}
      h2{{color:#4a9eff;margin-bottom:16px}}
      .info{{color:#555;margin-bottom:16px}}
      table{{border-collapse:collapse;width:100%}}
      th{{text-align:left;color:#555;padding:6px 12px;border-bottom:1px solid #2a2a2a}}
      td{{padding:5px 12px;border-bottom:1px solid #1a1a1a}}
      tr:hover td{{background:#1a1a1a}}
    </style></head><body>
    <h2>Live Queue</h2>
    <div class='info'>Server time: {now_str} — auto-refreshes every 10s</div>
    <table>
      <tr><th>Word</th><th>Pinyin</th><th>Category</th><th>State</th><th>Step</th><th>Due</th><th>Interval</th><th>Reps</th><th>Ease</th></tr>
      {''.join(rows)}
    </table></body></html>"""
    return HTMLResponse(html)


@app.get("/api/stats")
def get_stats():
    today = date.today().isoformat()
    return database.get_stats(today)


# ── CLI ────────────────────────────────────────────────────────────────────────

def cmd_import():
    database.init_db()
    result = importer.run_import()
    if "error" in result:
        print(f"Error: {result['error']}")
        return
    print(f"\nKnown vocab (no cards): {result['known']}")
    print(f"Words to learn (cards): {result['to_learn']}")
    print(f"Skipped:                {result['skipped']}")


def cmd_status():
    today = date.today().isoformat()
    stats = database.get_stats(today)
    print(f"Today: {today}")
    print(f"Total words in DB:  {stats['total_words']}")
    print(f"Active cards:       {stats['total_cards']}")
    print(f"Locked (creating):  {stats['locked_creating']}")
    print()
    print("Due today:")
    for cat in ("listening", "reading", "creating"):
        count = stats["due_today"][cat]
        print(f"  {cat:<12} {count:>4} cards")
    print()
    for cat in ("listening", "reading", "creating"):
        cards = database.get_due_cards(cat, today)
        if cards:
            print(f"-- {cat.upper()} (first 5) --")
            for c in cards[:5]:
                print(f"  [{c['state']:8}] {c['word_zh']:10} {c['pinyin']:20} {c['definition'][:40]}")


def cmd_reset():
    database.reset_all_cards()
    print("All cards reset to new, due today. Review log and daily content cleared.")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "import":
            cmd_import()
        elif cmd == "status":
            cmd_status()
        elif cmd == "reset":
            cmd_reset()
        else:
            print(f"Unknown command: {cmd}")
    else:
        database.init_db()
        database.reset_all_cards()
        print("Cards auto-reset for dev mode.")
        uvicorn.run(app, host="127.0.0.1", port=8000, reload=False)
