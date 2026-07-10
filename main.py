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

def _make_formatter() -> logging.Formatter:
    if not sys.stderr.isatty():
        return logging.Formatter(
            "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

    R = "\033[0m"
    DIM = "\033[2m"
    BOLD = "\033[1m"

    LEVEL_COLOR = {
        "DEBUG":    "\033[96m",    # bright cyan
        "INFO":     "\033[92m",    # bright green
        "WARNING":  "\033[93m",    # yellow
        "ERROR":    "\033[91m",    # bright red
        "CRITICAL": "\033[1;91m",  # bold red
    }
    LOGGER_COLOR = {
        "main":           "\033[34m",   # blue
        "routes.review":  "\033[1;96m", # bold cyan
        "routes.story":   "\033[35m",   # magenta
        "ai":             "\033[33m",   # orange/yellow
        "tts":            "\033[36m",   # cyan
        "importer":       "\033[37m",   # white
        "ui":             "\033[95m",   # bright magenta
    }
    METHOD_COLOR = {
        "POST":   "\033[34m",    # blue
        "GET":    "\033[32m",    # green
        "PUT":    "\033[33m",    # yellow
        "DELETE": "\033[31m",    # red
        "PATCH":  "\033[35m",    # magenta
    }

    class _ColorFmt(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            ts = f"{DIM}{self.formatTime(record, '%H:%M:%S')}{R}"
            msg = record.getMessage()
            parts = msg.split()

            # HTTP request lines — compact and dim
            if record.name == "main" and parts and parts[0] in METHOD_COLOR:
                mc = METHOD_COLOR[parts[0]]
                method = f"{mc}{BOLD}{parts[0]}{R}"
                rest = f"{DIM}{' '.join(parts[1:])}{R}"
                return f"{ts}  {method} {rest}"

            lc = LEVEL_COLOR.get(record.levelname, "")
            level = f"{lc}[{record.levelname:<5}]{R}"
            nc = LOGGER_COLOR.get(record.name, DIM)
            short = record.name.split(".")[-1]
            name = f"{nc}{short}{R}"
            return f"{ts} {level} {name}: {msg}"

    return _ColorFmt(datefmt="%H:%M:%S")


_handler = logging.StreamHandler(sys.stderr)
_handler.setFormatter(_make_formatter())
logging.root.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
logging.root.addHandler(_handler)
logger = logging.getLogger("main")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# In-memory ring buffer of recent log lines, exposed via GET /api/logs
# (issue #454) — lets Daniel check server logs from the settings page
# without SSH access.
# ---------------------------------------------------------------------------

import collections


class _RingBufferHandler(logging.Handler):
    def __init__(self, maxlen: int = 4000):
        super().__init__()
        self.buffer: "collections.deque[str]" = collections.deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.buffer.append(self.format(record))
        except Exception:
            pass  # never let logging itself raise


_log_buffer_handler = _RingBufferHandler(maxlen=4000)
# Always use the plain (non-colored) formatter, regardless of whether stderr
# is a tty, so the buffer never contains ANSI escape codes.
_log_buffer_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
))
logging.root.addHandler(_log_buffer_handler)


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
    import base64
    import binascii
    import secrets
    import time
    from contextlib import asynccontextmanager
    from fastapi import FastAPI, Request
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
    import uvicorn

    from routes import decks, review, story, browse, imports, podcast as podcast_routes

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

    ui_logger = logging.getLogger("ui")

    # Optional single-user HTTP Basic Auth — enabled only when both
    # AUTH_USERNAME and AUTH_PASSWORD are set (issue #419). If either is
    # missing, this middleware is a no-op — local dev behavior unchanged.
    _AUTH_USERNAME = os.environ.get("AUTH_USERNAME", "")
    _AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
    _AUTH_ENABLED = bool(_AUTH_USERNAME and _AUTH_PASSWORD)

    def _unauthorized() -> JSONResponse:
        return JSONResponse(
            status_code=401,
            content={"detail": "Unauthorized"},
            headers={"WWW-Authenticate": 'Basic realm="AnkiAdvanced"'},
        )

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if not _AUTH_ENABLED:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Basic "):
            return _unauthorized()

        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return _unauthorized()

        user_ok = secrets.compare_digest(username.encode("utf-8"), _AUTH_USERNAME.encode("utf-8"))
        pass_ok = secrets.compare_digest(password.encode("utf-8"), _AUTH_PASSWORD.encode("utf-8"))
        if not (user_ok and pass_ok):
            return _unauthorized()

        return await call_next(request)

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start = time.time()
        response = await call_next(request)
        ms = round((time.time() - start) * 1000)
        if request.url.path == "/api/log":
            return response
        if request.method != "GET" or response.status_code >= 400:
            params = dict(request.query_params)
            readable = {k: (v[:30] + "…" if len(v) > 30 else v) for k, v in params.items()}
            param_str = f"  {readable}" if readable else ""
            logger.info("%s %s%s → %d  (%dms)",
                        request.method, request.url.path, param_str,
                        response.status_code, ms)
        return response

    # Request-timing middleware (issue #458 measurement) — logs how long every
    # /api/ request actually takes, so slow endpoints show up in /api/logs
    # without needing SSH access. High-frequency polling endpoints are
    # excluded from routine logging (only surfaced if they somehow go slow),
    # since logging every poll would flood the ring buffer.
    _TIMING_QUIET_PREFIXES = (
        "/api/logs",
        "/api/story-progress",
        "/api/tts-progress",
        "/api/speak-status",
    )

    @app.middleware("http")
    async def request_timing(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)

        start = time.time()
        response = await call_next(request)
        ms = round((time.time() - start) * 1000)

        quiet = any(path.startswith(p) for p in _TIMING_QUIET_PREFIXES)
        if quiet:
            if ms > 500:
                logger.warning("SLOW %s %s: %d ms", request.method, path, ms)
            return response

        if ms > 500:
            logger.warning("SLOW %s %s: %d ms", request.method, path, ms)
        elif ms >= 100:
            logger.info("%s %s: %d ms", request.method, path, ms)
        else:
            logger.debug("%s %s: %d ms", request.method, path, ms)
        return response

    if os.path.exists("static"):
        app.mount("/static", StaticFiles(directory="static"), name="static")

    @app.get("/")
    def root():
        return FileResponse("static/index.html", headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        })

    # Running-version info (issue #450): read the current commit once at
    # startup — deploy.sh restarts the process on every deploy, so process
    # start time ≈ deploy time. Never fails: without git (or outside a
    # checkout) the badge just shows "unknown".
    def _read_version() -> dict:
        import subprocess
        try:
            log_out = subprocess.run(
                ["git", "log", "-1", "--format=%h%n%s%n%cI"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip().split("\n")
            branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=5, check=True,
            ).stdout.strip()
            return {"commit": log_out[0], "message": log_out[1],
                    "commit_date": log_out[2], "branch": branch}
        except Exception as e:
            logger.warning("version info unavailable (%s)", e)
            return {"commit": "unknown", "message": "", "commit_date": "", "branch": "unknown"}

    from datetime import datetime as _dt
    _version_info = {**_read_version(),
                     "deployed_at": _dt.now().isoformat(timespec="seconds")}

    @app.get("/api/version")
    def get_version():
        return _version_info

    app.include_router(decks.router)
    app.include_router(review.router)
    app.include_router(story.router)
    app.include_router(browse.router)
    app.include_router(imports.router)
    app.include_router(podcast_routes.router)

    import threading
    import time

    from pydantic import BaseModel

    class LogBody(BaseModel):
        action: str

    @app.post("/api/log")
    def log_ui_action(body: LogBody):
        ui_logger.info("点击 → %s", body.action)
        return {"ok": True}

    @app.get("/api/logs")
    def get_logs(lines: int = 500):
        n = max(1, min(lines, 4000))
        recent = list(_log_buffer_handler.buffer)[-n:]
        return PlainTextResponse("\n".join(recent))

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
        database.purge_old_trash()
        uvicorn.run(app, host="0.0.0.0", port=8000, access_log=False)
    else:
        print("Install fastapi and uvicorn to run the web server.")
        print("Usage: python main.py import | status [--deck NAME]")
