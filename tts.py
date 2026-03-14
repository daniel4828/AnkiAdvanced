import subprocess
import threading

_lock = threading.Lock()
_current: subprocess.Popen | None = None


def speak(text: str, rate: int = 175):
    """Play Chinese text via macOS Ting-Ting. Kills any currently playing audio first."""
    global _current
    with _lock:
        if _current and _current.poll() is None:
            _current.kill()
            _current.wait()
        _current = subprocess.Popen(["say", "-v", "Ting-Ting", "-r", str(rate), text])
    _current.wait()
