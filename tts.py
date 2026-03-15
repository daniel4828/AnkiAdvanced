"""
Text-to-speech wrapper using edge-tts + afplay (macOS).

speak() is fire-and-forget: it starts audio playback in a background thread
and returns immediately so the HTTP endpoint doesn't block.
"""

import asyncio
import os
import subprocess
import tempfile
import threading

import edge_tts

VOICE = "zh-CN-XiaoxiaoNeural"


def speak(text: str) -> None:
    """Start TTS playback in a background thread. Returns immediately."""
    t = threading.Thread(target=_run, args=(text,), daemon=True)
    t.start()


def _run(text: str) -> None:
    asyncio.run(_generate_and_play(text))


async def _generate_and_play(text: str) -> None:
    """Generate audio with edge-tts, save to a temp file, play with afplay."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    try:
        communicate = edge_tts.Communicate(text, VOICE)
        await communicate.save(tmp_path)
        subprocess.run(["afplay", tmp_path], check=True)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
