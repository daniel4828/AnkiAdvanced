"""Offline mode flag (issue #612).

Set OFFLINE_MODE=1 (see run.offline.sh) to run the app on a laptop with no
network at all — on a flight, for example. In this mode the app must never
open an outbound connection, because a hanging socket blocks the whole
request for as long as its timeout lasts.

What it turns off:
  - AI calls of every kind (implies DISABLE_AI — see routes/utils.py)
  - edge-tts generation: data/tts/ is served read-only, a cache miss is a
    fast 404 instead of a WebSocket attempt (see tts.py)
  - news fetching, translation, podcast processing (all AI/HTTP paths)

Everything already stored in the local database still works: reviews,
scheduling, browsing, stats, and any story + audio synced down beforehand.

This module deliberately has no imports beyond os so that anything can
import it without creating a cycle.
"""

import os

OFFLINE_MODE = os.getenv("OFFLINE_MODE", "").lower() in ("1", "true", "yes")
