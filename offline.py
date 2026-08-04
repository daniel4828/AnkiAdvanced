"""Local / offline mode flags (issues #612, #625).

Two different laptop modes, plus the server's normal mode:

  OFFLINE_MODE=1  (run.offline.sh)  — hard offline, for a flight.
      The app must never open an outbound connection, because a hanging
      socket blocks the whole request for as long as its timeout lasts.
      Not even the connectivity probe below runs.

  LOCAL_MODE=1    (run.local.sh)    — the everyday laptop instance.
      Fully featured while the network is up (AI stories, edge-tts
      generation, translation), and degrades to the OFFLINE_MODE behaviour
      by itself once the network goes away. Reachability is decided by a
      cached TCP probe rather than by a flag, so pulling the Wi-Fi plug is
      handled without a restart.

  neither                           — the server. Always considered online.

What "offline" turns off, in both laptop modes:
  - AI calls of every kind (implies DISABLE_AI — see routes/utils.py)
  - edge-tts generation: data/tts/ is served read-only, a cache miss is a
    fast 404 instead of a WebSocket attempt (see tts.py)
  - news fetching, translation, podcast processing (all AI/HTTP paths)

Everything stored in the local database still works either way: reviews,
scheduling, browsing, stats, and any story + audio synced down beforehand.

This module deliberately imports nothing from the app so that anything can
import it without creating a cycle.
"""

import os
import socket
import threading
import time

OFFLINE_MODE = os.getenv("OFFLINE_MODE", "").lower() in ("1", "true", "yes")
LOCAL_MODE = os.getenv("LOCAL_MODE", "").lower() in ("1", "true", "yes")

# Any one of these answering on 443 means "the network is usable". DeepSeek
# covers the AI path and the Bing endpoint covers edge-tts; both are reachable
# from China without a VPN, which the GitHub/Google hosts are not.
_PROBE_HOSTS = [
    h.strip() for h in os.getenv(
        "LOCAL_MODE_PROBE_HOSTS", "api.deepseek.com,speech.platform.bing.com"
    ).split(",") if h.strip()
]
_PROBE_PORT = 443
_PROBE_TIMEOUT = 1.5

# Asymmetric on purpose: while online we can afford a stale answer for a
# minute, but once offline we want to notice the Wi-Fi coming back quickly.
_TTL_ONLINE = 60.0
_TTL_OFFLINE = 10.0

_probe_lock = threading.Lock()
_probe_cache: tuple[bool, float] | None = None   # (online, expires_at)


def _probe() -> bool:
    for host in _PROBE_HOSTS:
        try:
            socket.create_connection((host, _PROBE_PORT), _PROBE_TIMEOUT).close()
            return True
        except OSError:
            continue
    return False


def network_available() -> bool:
    """True if an outbound connection is likely to succeed.

    Only ever probes in LOCAL_MODE. The server answers True without touching
    the network, and a hard-offline instance answers False the same way.
    The probe runs under a lock so a burst of concurrent requests waits for
    one probe instead of starting a dozen.
    """
    if OFFLINE_MODE:
        return False
    if not LOCAL_MODE:
        return True
    global _probe_cache
    with _probe_lock:
        now = time.monotonic()
        if _probe_cache is not None and now < _probe_cache[1]:
            return _probe_cache[0]
        online = _probe()
        _probe_cache = (online, now + (_TTL_ONLINE if online else _TTL_OFFLINE))
        return online


def is_offline() -> bool:
    """True when no outbound call may be attempted. The check every caller wants."""
    return not network_available()


def invalidate_network_cache() -> None:
    """Force the next network_available() to probe again — called after a sync,
    when the user has just demonstrated that the network works."""
    global _probe_cache
    with _probe_lock:
        _probe_cache = None
