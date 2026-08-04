"""One-click sync with the server (issue #625) — laptop instances only.

The heavy lifting stays in sync_offline.sh: it already knows the ssh/scp/rsync
incantations and prints numbered Chinese progress steps. This module runs that
script in a background thread and streams its output to the frontend, so the
button shows exactly what the terminal would have shown.

These routes are registered only when LOCAL_MODE or OFFLINE_MODE is set (see
main.py). On the server they do not exist at all — syncing "the server with
itself" is meaningless, and the script would happily overwrite production.
"""

import logging
import os
import subprocess
import threading
from datetime import datetime

from fastapi import APIRouter, HTTPException

import database
from offline import invalidate_network_cache

from .utils import queue_mgr

logger = logging.getLogger(__name__)
router = APIRouter()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Enough to hold a full run's output; the tts step is the only chatty one and
# it prints a handful of lines now that --progress is off for pipes.
_MAX_LINES = 2000

_lock = threading.Lock()
_state: dict = {
    "running": False,
    "lines": [],
    "ok": None,          # None while running, then True/False
    "error": None,
    "started_at": None,
    "finished_at": None,
}


def _append(line: str) -> None:
    with _lock:
        if len(_state["lines"]) < _MAX_LINES:
            _state["lines"].append(line)
        elif len(_state["lines"]) == _MAX_LINES:
            _state["lines"].append("…（输出过长，后续行已省略）")


def _run_sync(action: str) -> None:
    ok = False
    error = None
    try:
        proc = subprocess.Popen(
            ["bash", "sync_offline.sh", action],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1,
        )
        for line in proc.stdout:
            _append(line.rstrip("\n"))
        code = proc.wait()
        ok = code == 0
        if not ok:
            error = f"同步脚本以退出码 {code} 结束"
    except Exception as e:                       # noqa: BLE001 — surfaced to the UI
        logger.exception("sync failed")
        error = f"{type(e).__name__}: {e}"
    finally:
        if ok:
            # The database file underneath us was just replaced, and the sync
            # itself proved the network works.
            queue_mgr.invalidate()
            database.refresh_day_cutoff_hour()
            invalidate_network_cache()
        with _lock:
            _state.update(running=False, ok=ok, error=error,
                          finished_at=datetime.now().isoformat(timespec="seconds"))


@router.post("/api/sync/start")
def start_sync(mode: str = "sync"):
    """Kick off a sync. Returns immediately; poll /api/sync/progress.

    mode='sync'  push local reviews, then pull the fresh database (the normal path)
    mode='pull'  download only, discarding whatever is in the local database.
                 The escape hatch for a local copy the server refuses to merge —
                 no sync token (it never came from a pull) or a rotated one (it
                 was already pushed). Merging those is correctly refused, but
                 without this the button would be stuck failing forever.
    """
    if mode not in ("sync", "pull"):
        raise HTTPException(400, f"unknown sync mode {mode!r}")
    with _lock:
        if _state["running"]:
            raise HTTPException(409, "A sync is already running")
        _state.update(running=True, lines=[], ok=None, error=None,
                      started_at=datetime.now().isoformat(timespec="seconds"),
                      finished_at=None)
    threading.Thread(target=_run_sync, args=(mode,), daemon=True).start()
    return {"started": True, "mode": mode}


@router.get("/api/sync/progress")
def sync_progress():
    with _lock:
        return {
            "running": _state["running"],
            "lines": list(_state["lines"]),
            "ok": _state["ok"],
            "error": _state["error"],
            "started_at": _state["started_at"],
            "finished_at": _state["finished_at"],
        }
