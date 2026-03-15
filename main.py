import argparse
import os
import sys

import database
import importer


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_import(args):
    print("Importing from imports/...")
    result = importer.import_all("imports")
    print(f"Done — imported {result['imported']} words "
          f"({result['skipped_duplicate']} skipped as duplicates)")


def cmd_status(args):
    decks = database.get_all_decks()
    if args.deck:
        decks = [d for d in decks if d["name"].lower() == args.deck.lower()]
        if not decks:
            print(f"No deck named '{args.deck}'")
            return

    categories = ["reading", "listening", "creating"]
    header = f"{'Deck':<20} {'Category':<12} {'New':>5} {'Learning':>9} {'Review':>7}"
    print(header)
    print("-" * len(header))

    for deck in decks:
        for cat in categories:
            counts = database.count_due(deck["id"], cat)
            print(
                f"{deck['name']:<20} {cat:<12} "
                f"{counts['new']:>5} {counts['learning']:>9} {counts['review']:>7}"
            )


def main():
    database.init_db()

    parser = argparse.ArgumentParser(description="Chinese SRS")
    sub = parser.add_subparsers(dest="command")

    # import
    sub.add_parser("import", help="Import vocabulary from imports/")

    # status
    status_p = sub.add_parser("status", help="Show due counts per deck/category")
    status_p.add_argument("--deck", help="Filter to a specific deck name")

    args = parser.parse_args()

    if args.command == "import":
        cmd_import(args)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


# ---------------------------------------------------------------------------
# FastAPI app (stubs for M2+)
# ---------------------------------------------------------------------------

try:
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import JSONResponse, FileResponse
    import uvicorn

    app = FastAPI(title="Chinese SRS")

    @app.on_event("shutdown")
    def on_shutdown():
        if os.environ.get("DEV_CLEAR_DB"):
            try:
                os.unlink(database.DB_PATH)
                print("[dev] Database cleared on exit.", file=sys.stderr)
            except FileNotFoundError:
                pass

    import os as _os
    if _os.path.exists("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def root():
        return FileResponse("static/index.html")

    # --- Decks ---
    @app.get("/api/decks")
    def get_decks():
        tree = database.get_deck_tree()
        for deck in _flatten(tree):
            for cat in ("reading", "listening", "creating"):
                deck.setdefault("counts", {})[cat] = database.count_due(deck["id"], cat)
        return tree

    @app.post("/api/decks")
    def create_deck(name: str, parent_id: int | None = None):
        preset_id = database.get_preset_for_deck(
            database.get_default_deck_id()
        )["id"] if parent_id is None else \
            database.get_deck(parent_id)["preset_id"]
        deck_id = database.insert_deck(name, parent_id, preset_id)
        return database.get_deck(deck_id)

    @app.put("/api/decks/{deck_id}")
    def update_deck(deck_id: int, name: str | None = None, preset_id: int | None = None):
        if name:
            database.rename_deck(deck_id, name)
        return database.get_deck(deck_id)

    @app.get("/api/decks/{deck_id}/preset")
    def get_deck_preset(deck_id: int):
        return database.get_preset_for_deck(deck_id)

    @app.put("/api/decks/{deck_id}/preset")
    def update_deck_preset(deck_id: int, fields: dict):
        deck = database.get_deck(deck_id)
        database.update_preset(deck["preset_id"], fields)
        return database.get_preset(deck["preset_id"])

    # --- Review session ---
    @app.get("/api/today/{deck_id}/{category}")
    def get_today(deck_id: int, category: str):
        import srs
        card = database.get_next_card(deck_id, category)
        if card:
            # Fetch full card (includes preset fields needed for interval preview)
            card = database.get_card(card["id"])
            card["intervals"] = srs.preview_intervals(card)
        counts = database.count_due(deck_id, category)
        return {"card": card, "counts": counts}

    @app.get("/api/story/{deck_id}/{category}")
    def get_story(deck_id: int, category: str):
        import ai
        from datetime import date
        today = date.today().isoformat()
        story = database.get_active_story(today, category, deck_id)
        if not story:
            cards = database.get_due_cards(deck_id, category)
            if cards:
                try:
                    sentences = ai.generate_story(cards)
                    for i, s in enumerate(sentences):
                        s["position"] = i
                    database.create_story(today, category, deck_id, sentences)
                    story = database.get_active_story(today, category, deck_id)
                except Exception as _e:
                    import sys
                    print(f"[story] generation error: {_e}", file=sys.stderr)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
        return story

    @app.post("/api/story/{deck_id}/{category}/regenerate")
    def regenerate_story(deck_id: int, category: str):
        import ai
        from datetime import date
        today = date.today().isoformat()
        cards = database.get_due_cards(deck_id, category)
        if not cards:
            return None
        sentences = ai.generate_story(cards)
        for i, s in enumerate(sentences):
            s["position"] = i
        database.create_story(today, category, deck_id, sentences)
        story = database.get_active_story(today, category, deck_id)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
        return story

    @app.post("/api/review")
    def submit_review(card_id: int, rating: int, user_response: str | None = None):
        import srs
        updated = srs.apply_review(card_id, rating, user_response=user_response)
        deck_id = updated["deck_id"]
        cat = updated["category"]
        next_card = database.get_next_card(deck_id, cat)
        if next_card:
            next_card = database.get_card(next_card["id"])
            next_card["intervals"] = srs.preview_intervals(next_card)
        counts = database.count_due(deck_id, cat)
        return {"next_card": next_card, "counts": counts}

    @app.post("/api/speak")
    def speak(text: str):
        import tts
        try:
            tts.speak(text)
        except Exception:
            pass  # TTS is best-effort; never break the review session
        return {"ok": True}

    @app.post("/api/preload")
    def preload(text: str):
        """Pre-generate TTS audio without playing — call when a card loads."""
        import tts
        try:
            tts.preload(text)
        except Exception:
            pass
        return {"ok": True}

    @app.post("/api/preload-session/{deck_id}/{category}")
    def preload_session(deck_id: int, category: str):
        """Pre-generate TTS for every sentence in today's active story in parallel."""
        import tts
        from datetime import date
        today = date.today().isoformat()
        story = database.get_active_story(today, category, deck_id)
        if story:
            sentences = database.get_story_sentences(story["id"])
            texts = [s["sentence_zh"] for s in sentences if s.get("sentence_zh")]
            try:
                tts.preload_all(texts)
            except Exception:
                pass
        return {"ok": True}

    @app.post("/api/import")
    def trigger_import():
        result = importer.import_all("imports")
        return result

    @app.get("/api/word/{word_id}")
    def get_word_detail(word_id: int):
        return database.get_word_full(word_id)

    @app.get("/api/browse")
    def browse(deck_id: int | None = None, category: str | None = None,
               state: str | None = None, q: str | None = None):
        filters = {
            "deck_id": deck_id,
            "category": category,
            "state": state,
            "search_text": q,
        }
        return database.get_all_cards_for_browse(filters)

    @app.get("/api/stats")
    def get_stats(deck_id: int | None = None):
        return database.get_stats(deck_id)

    def _flatten(tree: list) -> list:
        result = []
        for node in tree:
            result.append(node)
            result.extend(_flatten(node.get("children", [])))
        return result

except ImportError:
    app = None  # FastAPI not installed yet


if __name__ == "__main__":
    if len(sys.argv) > 1:
        main()
    elif app:
        import uvicorn
        database.init_db()
        uvicorn.run(app, host="0.0.0.0", port=8000)
    else:
        print("Install fastapi and uvicorn to run the web server.")
        print("Usage: python main.py import | status [--deck NAME]")
