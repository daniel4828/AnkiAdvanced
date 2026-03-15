"""
Text-to-speech wrapper using edge-tts + afplay (macOS).

speak()   — play audio for text (uses cache if already generated)
preload() — pre-generate audio in background without playing yet

Caching: generated .mp3 files are kept in memory (text → file path).
This means speak() after preload() plays instantly with no network wait.
Cache is limited to 5 entries; oldest entry is evicted when full.
"""

import asyncio
import os
import subprocess
import tempfile
import threading

import edge_tts

VOICE = "zh-CN-XiaoxiaoNeural"

# Simple text → file-path cache so the second play is instant
_cache: dict[str, str] = {}


def preload(text: str) -> None:
    """Generate audio in the background without playing. Call when a card loads."""
    threading.Thread(target=lambda: asyncio.run(_ensure_cached(text)),
                     daemon=True).start()


def speak(text: str) -> None:
    """Play audio. Uses cached file if preload() already ran for this text."""
    threading.Thread(target=lambda: asyncio.run(_play(text)),
                     daemon=True).start()


async def _ensure_cached(text: str) -> str:
    """Generate audio and store in cache. Returns the file path."""
    cached = _cache.get(text)
    if cached and os.path.exists(cached):
        return cached

    f = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = f.name
    f.close()

    communicate = edge_tts.Communicate(text, VOICE)
    await communicate.save(tmp_path)

    # Evict oldest entry if cache is full
    if len(_cache) >= 5:
        oldest_text = next(iter(_cache))
        try:
            os.unlink(_cache.pop(oldest_text))
        except OSError:
            pass

    _cache[text] = tmp_path
    return tmp_path


async def _play(text: str) -> None:
    tmp_path = await _ensure_cached(text)
    subprocess.run(["afplay", tmp_path], check=True)
