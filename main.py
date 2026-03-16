import argparse
import logging
import os
import sys

import database
import importer

# ---------------------------------------------------------------------------
# Logging — set LOG_LEVEL=DEBUG in .env for verbose output
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger("main")


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
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import JSONResponse, FileResponse
    import uvicorn

    @asynccontextmanager
    async def lifespan(app):
        yield  # startup (nothing to do)
        if os.environ.get("DEV_CLEAR_DB"):
            import shutil
            import tts as _tts
            try:
                os.unlink(database.DB_PATH)
            except FileNotFoundError:
                pass
            try:
                shutil.rmtree(_tts.TTS_CACHE_DIR)
            except FileNotFoundError:
                pass
            logger.info("[dev] DB and TTS cache cleared on exit.")

    app = FastAPI(title="Chinese SRS", lifespan=lifespan)

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
        # Attach counts to every deck in the tree bottom-up
        _attach_counts(_flatten(tree))
        return tree

    def _attach_counts(flat_decks: list) -> None:
        """Compute due counts for leaf decks; aggregate upward for parents."""
        by_id = {d["id"]: d for d in flat_decks}
        # Process leaves first (no children)
        for deck in flat_decks:
            if not deck.get("children"):
                cat = deck.get("category")
                if cat:
                    deck["counts"] = database.count_due(deck["id"], cat)
                else:
                    deck["counts"] = {"new": 0, "learning": 0, "review": 0}
        # Aggregate parents bottom-up (tree may be multi-level)
        for deck in reversed(flat_decks):
            children = deck.get("children", [])
            if children:
                agg = {"new": 0, "learning": 0, "review": 0}
                for child in children:
                    for k in agg:
                        agg[k] += child.get("counts", {}).get(k, 0)
                deck["counts"] = agg

    @app.post("/api/decks")
    def create_deck(name: str, parent_id: int | None = None,
                    category: str | None = None):
        preset_id = database.get_preset_for_deck(
            database.get_default_deck_id()
        )["id"] if parent_id is None else \
            database.get_deck(parent_id)["preset_id"]
        deck_id = database.insert_deck(name, parent_id, preset_id, category)
        return database.get_deck(deck_id)

    @app.put("/api/decks/{deck_id}")
    def update_deck(deck_id: int, name: str | None = None, preset_id: int | None = None):
        if name:
            database.rename_deck(deck_id, name)
        return database.get_deck(deck_id)

    @app.get("/api/presets")
    def list_presets():
        return database.list_presets()

    @app.post("/api/presets")
    def create_preset(name: str, clone_from_id: int | None = None):
        if clone_from_id:
            src = database.get_preset(clone_from_id)
        else:
            src = database.default_preset()
        src["name"] = name
        src.pop("id", None)
        src.pop("is_default", None)
        src.pop("deck_count", None)
        preset_id = database.insert_preset(src)
        return database.get_preset(preset_id)

    @app.delete("/api/presets/{preset_id}")
    def delete_preset(preset_id: int):
        try:
            database.delete_preset(preset_id)
            return {"ok": True}
        except ValueError as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=str(e))

    @app.get("/api/decks/{deck_id}/preset")
    def get_deck_preset(deck_id: int):
        return database.get_preset_for_deck(deck_id)

    @app.put("/api/decks/{deck_id}/preset")
    def update_deck_preset(deck_id: int, fields: dict):
        deck = database.get_deck(deck_id)
        database.update_preset(deck["preset_id"], fields)
        return database.get_preset(deck["preset_id"])

    @app.put("/api/decks/{deck_id}/preset/assign")
    def assign_preset_to_deck(deck_id: int, preset_id: int):
        database.assign_preset_to_deck(deck_id, preset_id)
        return database.get_preset(preset_id)

    @app.post("/api/decks/{deck_id}/preset/set-default")
    def set_deck_preset_as_default(deck_id: int):
        deck = database.get_deck(deck_id)
        database.set_default_preset(deck["preset_id"])
        return database.get_preset(deck["preset_id"])

    _DISABLE_AI = os.getenv("DISABLE_AI", "").lower() in ("1", "true", "yes")

    def _leaf_ids(deck_id: int, category: str) -> list[int]:
        """If deck is a parent (no category), return descendant leaf IDs; else [deck_id]."""
        deck = database.get_deck(deck_id)
        if deck["category"] is None:
            return database.get_descendant_leaf_deck_ids(deck_id, category)
        return [deck_id]

    # --- Review session ---
    @app.get("/api/today/{deck_id}/{category}")
    def get_today(deck_id: int, category: str):
        import srs
        ids = _leaf_ids(deck_id, category)
        if len(ids) == 1:
            card = database.get_next_card(ids[0], category)
            counts = database.count_due(ids[0], category)
        else:
            card = database.get_next_card_multi(ids, category)
            counts = database.count_due_multi(ids, category)
        if card:
            card = database.get_card(card["id"])
            card["intervals"] = srs.preview_intervals(card)
        return {"card": card, "counts": counts}

    @app.get("/api/story/{deck_id}/{category}")
    def get_story(deck_id: int, category: str):
        import ai
        from datetime import date
        today = date.today().isoformat()
        story = database.get_active_story(today, category, deck_id)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
            logger.info("story  CACHED  deck=%d cat=%s sentences=%d story_id=%d",
                        deck_id, category, len(story["sentences"]), story["id"])
            _log_story(story)
            return story
        if _DISABLE_AI:
            logger.info("story  DISABLED (DISABLE_AI=1) deck=%d cat=%s", deck_id, category)
            return None
        ids = _leaf_ids(deck_id, category)
        cards = database.get_due_cards_multi(ids, category) if len(ids) > 1 else database.get_due_cards(deck_id, category)
        logger.info("story  GENERATE deck=%d cat=%s due_cards=%d", deck_id, category, len(cards))
        if cards:
            try:
                preset = database.get_preset_for_deck(ids[0] if ids else deck_id)
                if preset.get("randomize_story_order", 1):
                    import random as _random
                    _random.shuffle(cards)
                sentences = ai.generate_story(cards)
                for i, s in enumerate(sentences):
                    s["position"] = i
                database.create_story(today, category, deck_id, sentences)
                story = database.get_active_story(today, category, deck_id)
            except Exception as _e:
                logger.error("story  generation error: %s", _e)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
            logger.info("story  SAVED   deck=%d cat=%s sentences=%d",
                        deck_id, category, len(story["sentences"]))
            _log_story(story)
        return story

    @app.post("/api/story/{deck_id}/{category}/regenerate")
    def regenerate_story(deck_id: int, category: str):
        import ai
        from datetime import date
        today = date.today().isoformat()
        if _DISABLE_AI:
            return None
        ids = _leaf_ids(deck_id, category)
        cards = database.get_due_cards_multi(ids, category) if len(ids) > 1 else database.get_due_cards(deck_id, category)
        logger.info("regen  deck=%d cat=%s due_cards=%d", deck_id, category, len(cards))
        if not cards:
            return None
        preset = database.get_preset_for_deck(ids[0] if ids else deck_id)
        if preset.get("randomize_story_order", 1):
            import random as _random
            _random.shuffle(cards)
        sentences = ai.generate_story(cards)
        for i, s in enumerate(sentences):
            s["position"] = i
        database.create_story(today, category, deck_id, sentences)
        story = database.get_active_story(today, category, deck_id)
        if story:
            story["sentences"] = database.get_story_sentences(story["id"])
            logger.info("regen  SAVED sentences=%d", len(story["sentences"]))
            _log_story(story)
        return story

    @app.post("/api/review")
    def submit_review(card_id: int, rating: int, user_response: str | None = None):
        import srs
        card_before = database.get_card(card_id)
        updated = srs.apply_review(card_id, rating, user_response=user_response)
        deck_id = updated["deck_id"]
        cat = updated["category"]
        preset = database.get_preset_for_deck(deck_id)
        if preset.get("bury_siblings", 1):
            database.bury_siblings(updated["word_id"], cat)
        next_card = database.get_next_card(deck_id, cat)
        if next_card:
            next_card = database.get_card(next_card["id"])
            next_card["intervals"] = srs.preview_intervals(next_card)
        counts = database.count_due(deck_id, cat)
        rating_label = {1: "Again", 2: "Hard", 3: "Good", 4: "Easy"}.get(rating, rating)
        logger.info("review %s → %s (%s)  due=%s  next=%s  queue: %d lrn %d rev %d new",
                    card_before["word_zh"], updated["state"], rating_label,
                    updated["due"], next_card["word_zh"] if next_card else "—",
                    counts["learning"], counts["review"], counts["new"])
        return {"next_card": next_card, "counts": counts}

    @app.post("/api/cards/{card_id}/bury")
    def bury_card(card_id: int):
        database.bury_card(card_id)
        return {"ok": True}

    @app.post("/api/cards/{card_id}/unbury")
    def unbury_card(card_id: int):
        database.unbury_card(card_id)
        return {"ok": True}

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
    async def preload_session(deck_id: int, category: str):
        """Pre-generate TTS for every sentence — waits until all audio is cached."""
        import tts
        from datetime import date
        today = date.today().isoformat()
        story = database.get_active_story(today, category, deck_id)
        if story:
            sentences = database.get_story_sentences(story["id"])
            texts = [s["sentence_zh"] for s in sentences if s.get("sentence_zh")]
            logger.info("tts  preloading %d sentences", len(texts))
            try:
                await tts.preload_all_async(texts)
                logger.info("tts  preload done")
            except Exception as _e:
                logger.warning("tts  preload error: %s", _e)
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

    def _log_story(story: dict) -> None:
        """Log full story sentences at DEBUG level."""
        if not logger.isEnabledFor(logging.DEBUG):
            return
        sentences = story.get("sentences", [])
        lines = [f"  Story id={story['id']} ({len(sentences)} sentences):"]
        for s in sentences:
            lines.append(f"    {s['position']+1}. {s['sentence_zh']}")
            lines.append(f"       {s['sentence_en']}")
        logger.debug("\n".join(lines))

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
