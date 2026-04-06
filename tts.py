"""
Text-to-speech wrapper using edge-tts + afplay (macOS).

speak()            — play audio for text (instant if already cached)
preload()          — pre-generate audio in background without playing
preload_all_async()— pre-generate a batch in parallel; awaitable

Cache strategy: persistent files in data/tts/<sha256(text)>.mp3
  - Survives server restarts — same sentence is never generated twice
  - Atomic write (tmp → rename) prevents partial files
  - No size limit (mp3 files are ~30–100 KB each)
  - Small in-memory set tracks which paths we've verified this process,
    to skip the os.path.exists call for hot items
"""

import asyncio
import hashlib
import logging
import os
import subprocess
import threading

import edge_tts

# Bypass system proxy (e.g. Shadowrocket in China) for edge-tts WebSocket connections.
# The VPN tunnel routes traffic at the network level, so direct connections work fine.
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

logger = logging.getLogger(__name__)

VOICE = "zh-CN-XiaoxiaoNeural"
TTS_CACHE_DIR = "data/tts"

# In-process set of paths confirmed to exist — avoids repeated stat() calls
_hot: set[str] = set()


def _cache_path(text: str) -> str:
    key = hashlib.sha256(text.encode()).hexdigest()
    return os.path.join(TTS_CACHE_DIR, f"{key}.mp3")


async def _ensure_cached(text: str) -> str:
    """Return path to mp3 for text, generating via edge-tts if not on disk."""
    path = _cache_path(text)
    if path in _hot or os.path.exists(path):
        _hot.add(path)
        return path

    os.makedirs(TTS_CACHE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    logger.debug("tts  generating %r → %s", text[:30], os.path.basename(path))
    communicate = edge_tts.Communicate(text, VOICE)
    try:
        await communicate.save(tmp)
        if not os.path.exists(tmp):
            raise RuntimeError("edge-tts produced no output")
        os.replace(tmp, path)   # atomic: no partial files visible to readers
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _hot.add(path)
    logger.debug("tts  cached     %s", os.path.basename(path))
    return path


async def preload_all_async(texts: list[str]) -> None:
    """Pre-generate audio for all texts in parallel. Awaitable — blocks until done."""
    missing = [t for t in texts if _cache_path(t) not in _hot
               and not os.path.exists(_cache_path(t))]
    if not missing:
        logger.info("tts  all %d sentences already cached", len(texts))
        return
    logger.info("tts  generating %d/%d sentences (rest cached)",
                len(missing), len(texts))
    await asyncio.gather(*[_ensure_cached(t) for t in texts])


def preload(text: str) -> None:
    """Fire-and-forget single-item preload (background thread)."""
    threading.Thread(target=lambda: asyncio.run(_ensure_cached(text)),
                     daemon=True).start()


_current_playback: subprocess.Popen | None = None
_playback_lock = threading.Lock()
_stop_requested = False
_multi_lock = threading.Lock()   # only one speak_multi runs at a time
_current_play_idx: int = -1
_is_multi_playing: bool = False


async def get_cached_path(text: str) -> str:
    """Return local mp3 path for text, generating via edge-tts if not cached."""
    return await _ensure_cached(text)


def get_status() -> dict:
    """Return current playback index and whether speak_multi is active."""
    return {"idx": _current_play_idx, "playing": _is_multi_playing}


def speak(text: str) -> None:
    """Play audio, killing any ongoing playback first (fire-and-forget)."""
    threading.Thread(target=lambda: asyncio.run(_play(text)),
                     daemon=True).start()


def speak_sync(text: str) -> None:
    """Play audio and block until playback is complete."""
    asyncio.run(_play(text))


def speak_multi(texts: list[str], start_idx: int = 0) -> None:
    """Play texts[start_idx:] sequentially with minimal gap.

    Sets _stop_requested before acquiring _multi_lock so any in-progress
    speak_multi exits quickly, then starts fresh.
    """
    global _stop_requested
    _stop_requested = True   # interrupt any current playback fast
    with _multi_lock:
        _stop_requested = False  # safe to start now — old loop has exited
        asyncio.run(_play_multi(texts, start_idx))


async def _play_multi(texts: list[str], start_idx: int = 0) -> None:
    """Pre-cache all sentences in parallel, then play from start_idx."""
    global _stop_requested, _current_play_idx, _is_multi_playing
    await preload_all_async(texts)
    _is_multi_playing = True
    try:
        for i, text in enumerate(texts[start_idx:], start=start_idx):
            if _stop_requested:
                break
            _current_play_idx = i
            await _play(text)
    finally:
        _is_multi_playing = False
        _current_play_idx = -1


def stop() -> None:
    """Stop any ongoing playback and cancel speak_multi loop."""
    global _current_playback, _stop_requested
    _stop_requested = True
    with _playback_lock:
        if _current_playback and _current_playback.poll() is None:
            _current_playback.kill()
            _current_playback.wait()


SAY_VOICE = "Tingting"  # macOS built-in zh_CN fallback


async def _play(text: str) -> None:
    global _current_playback
    try:
        path = await asyncio.wait_for(_ensure_cached(text), timeout=5.0)
        cmd = ["afplay", path]
    except Exception:
        logger.warning("tts  edge-tts failed, falling back to macOS say")
        cmd = ["say", "-v", SAY_VOICE, text]

    with _playback_lock:
        if _current_playback and _current_playback.poll() is None:
            _current_playback.kill()
            _current_playback.wait()
        _current_playback = subprocess.Popen(cmd)
    _current_playback.wait()
