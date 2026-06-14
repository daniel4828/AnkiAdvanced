"""FSRS-5 — Free Spaced Repetition Scheduler.

Pure-Python implementation of the DSR memory model (Difficulty, Stability,
Retrievability) used by modern Anki. No external dependencies.

This module is intentionally side-effect free: every function takes plain
numbers and returns plain numbers. srs.py orchestrates how these results map
onto card state transitions and the database.

Model in one paragraph:
  - Stability (S): days until recall probability drops to 90%. Larger = sturdier.
  - Difficulty (D): intrinsic hardness on a 1–10 scale; mean-reverts each review
    so a card can never get permanently stuck at maximum difficulty (no "ease hell").
  - Retrievability (R): probability of recall right now, given elapsed days and S.

Scheduling: pick a desired retention (e.g. 0.9) and the next interval is the
number of days after which R will have decayed to exactly that target.
"""

import math

# Forgetting curve constants. DECAY fixes the shape; FACTOR is derived so that
# R(t=S) == 0.9 exactly (i.e. stability is the days-to-90% by definition).
DECAY = -0.5
FACTOR = 0.9 ** (1 / DECAY) - 1  # = 19/81 ≈ 0.2345679

# Stability is clamped to this range to avoid degenerate intervals.
S_MIN = 0.01
S_MAX = 36500.0

# FSRS-5 default parameters (19 weights), the values Anki ships out of the box.
# w[0..3]  : initial stability per rating (Again/Hard/Good/Easy)
# w[4..5]  : initial difficulty curve
# w[6..7]  : difficulty update (damping + mean reversion)
# w[8..10] : stability increase on successful recall
# w[11..14]: stability after a lapse (forgetting)
# w[15]    : Hard penalty   (<1, shrinks the recall-stability gain)
# w[16]    : Easy bonus     (>1, grows the recall-stability gain)
# w[17..18]: short-term (same-day) stability adjustment
DEFAULT_WEIGHTS = [
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604,
    0.0046, 1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605,
    2.2698, 0.2315, 2.9898, 0.51655, 0.6621,
]


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def parse_weights(weights_str: str | None) -> list[float]:
    """Parse a space-separated weights string; fall back to defaults if malformed."""
    if not weights_str:
        return list(DEFAULT_WEIGHTS)
    try:
        parsed = [float(x) for x in weights_str.strip().split()]
    except ValueError:
        return list(DEFAULT_WEIGHTS)
    if len(parsed) != len(DEFAULT_WEIGHTS):
        return list(DEFAULT_WEIGHTS)
    return parsed


# ---------------------------------------------------------------------------
# Initial state (first time a card graduates out of learning)
# ---------------------------------------------------------------------------

def init_stability(w: list[float], rating: int) -> float:
    """Stability right after the first rating (rating in 1..4)."""
    return _clamp(w[rating - 1], S_MIN, S_MAX)


def init_difficulty(w: list[float], rating: int) -> float:
    """Difficulty right after the first rating (rating in 1..4)."""
    return _clamp(w[4] - math.exp(w[5] * (rating - 1)) + 1, 1.0, 10.0)


# ---------------------------------------------------------------------------
# Retrievability & interval
# ---------------------------------------------------------------------------

def retrievability(elapsed_days: float, stability: float) -> float:
    """Probability of recall after `elapsed_days` given `stability`."""
    if stability <= 0:
        return 0.0
    return (1 + FACTOR * elapsed_days / stability) ** DECAY


def next_interval(stability: float, desired_retention: float,
                  maximum_interval: int = 36500) -> int:
    """Days until retrievability decays to `desired_retention`. Clamped to [1, max]."""
    desired_retention = _clamp(desired_retention, 0.70, 0.99)
    raw = stability / FACTOR * (desired_retention ** (1 / DECAY) - 1)
    return int(_clamp(round(raw), 1, maximum_interval))


# ---------------------------------------------------------------------------
# State updates
# ---------------------------------------------------------------------------

def next_difficulty(w: list[float], difficulty: float, rating: int) -> float:
    """Update difficulty. Uses linear damping + mean reversion toward the
    Easy-rating anchor, so difficulty drifts back to centre over time."""
    delta = -w[6] * (rating - 3)
    damped = difficulty + delta * (10 - difficulty) / 9  # linear damping
    anchor = init_difficulty(w, 4)
    reverted = w[7] * anchor + (1 - w[7]) * damped       # mean reversion
    return _clamp(reverted, 1.0, 10.0)


def next_recall_stability(w: list[float], difficulty: float, stability: float,
                          r: float, rating: int) -> float:
    """New stability after a successful recall (rating Hard/Good/Easy)."""
    hard_penalty = w[15] if rating == 2 else 1.0
    easy_bonus = w[16] if rating == 4 else 1.0
    gain = (
        math.exp(w[8])
        * (11 - difficulty)
        * (stability ** -w[9])
        * (math.exp((1 - r) * w[10]) - 1)
        * hard_penalty
        * easy_bonus
    )
    return _clamp(stability * (1 + gain), S_MIN, S_MAX)


def next_forget_stability(w: list[float], difficulty: float, stability: float,
                          r: float) -> float:
    """New stability after a lapse (rating Again). Drops, but recovers later —
    never crushed toward zero the way SM-2 halves the interval."""
    s_forget = (
        w[11]
        * (difficulty ** -w[12])
        * ((stability + 1) ** w[13] - 1)
        * math.exp((1 - r) * w[14])
    )
    # Post-lapse stability must not exceed the pre-lapse value.
    return _clamp(min(s_forget, stability), S_MIN, S_MAX)


def next_short_term_stability(w: list[float], stability: float, rating: int) -> float:
    """Same-day (sub-interval) stability bump, used for within-day re-reviews."""
    return _clamp(stability * math.exp(w[17] * (rating - 3 + w[18])), S_MIN, S_MAX)


# ---------------------------------------------------------------------------
# High-level helpers used by srs.py
# ---------------------------------------------------------------------------

def review_state(w: list[float], difficulty: float, stability: float,
                 elapsed_days: float, rating: int) -> tuple[float, float]:
    """Return (new_stability, new_difficulty) for a review-state card."""
    r = retrievability(elapsed_days, stability)
    new_d = next_difficulty(w, difficulty, rating)
    if rating == 1:
        new_s = next_forget_stability(w, difficulty, stability, r)
    else:
        new_s = next_recall_stability(w, difficulty, stability, r, rating)
    return new_s, new_d


def review_intervals(w: list[float], difficulty: float, stability: float,
                     elapsed_days: float, desired_retention: float,
                     maximum_interval: int = 36500) -> dict[int, int]:
    """Preview the resulting day-interval for each of Hard/Good/Easy on a
    review-state card. Again is handled separately (goes to relearn steps).

    The three intervals are forced monotonic and distinct so the buttons can
    never show an inverted or duplicated order (Hard ≤ Good < Easy)."""
    out = {}
    for rating in (2, 3, 4):
        new_s, _ = review_state(w, difficulty, stability, elapsed_days, rating)
        out[rating] = next_interval(new_s, desired_retention, maximum_interval)

    # Guarantee Hard ≤ Good < Easy even if weights/fuzz would invert them.
    out[3] = max(out[3], out[2])
    if out[3] <= out[2]:
        out[3] = out[2] + 1
    out[4] = max(out[4], out[3] + 1)
    return out
