import argparse
import logging
import os
import sys


def _load_env(path: str = ".env") -> None:
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:]
                key, _, val = line.partition("=")
                val = val.strip('"').strip("'")
                os.environ.setdefault(key.strip(), val)
    except FileNotFoundError:
        pass

_load_env()

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
    invalid = result.get("skipped_invalid", 0)
    invalid_str = f", {invalid} skipped as invalid" if invalid else ""
    print(f"Done — imported {result['imported']} words "
          f"({result['skipped_duplicate']} skipped as duplicates{invalid_str})")


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
            print(f"{deck['name']:<20} {cat:<12} "
                  f"{counts['new']:>5} {counts['learning']:>9} {counts['review']:>7}")


def main():
    database.init_db()

    parser = argparse.ArgumentParser(description="AnkiAdvanced")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("import", help="Import vocabulary from imports/")
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
# FastAPI app
# ---------------------------------------------------------------------------

try:
    from contextlib import asynccontextmanager
    from fastapi import FastAPI
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    import uvicorn

    from routes import decks, review, story, browse, imports

    @asynccontextmanager
    async def lifespan(app):
        yield  # startup
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

    app = FastAPI(title="AnkiAdvanced", lifespan=lifespan)

    if os.path.exists("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def root():
        return FileResponse("static/index.html")

    app.include_router(decks.router)
    app.include_router(review.router)
    app.include_router(story.router)
    app.include_router(browse.router)
    app.include_router(imports.router)

    import threading
    import time

    @app.post("/api/restart")
    def restart_server():
        def _do_restart():
            time.sleep(0.3)
            os.execv(sys.executable, [sys.executable] + sys.argv)
        threading.Thread(target=_do_restart, daemon=False).start()
        return {"ok": True}

except ImportError as e:
    import sys
    print(f"Import error (app disabled): {e}", file=sys.stderr)
    app = None  # FastAPI not installed


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
