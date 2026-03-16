import math
from datetime import date, datetime, timedelta

import database


# ---------------------------------------------------------------------------
# Interval preview — compute human-readable next-interval labels per rating
# Used by the frontend to label the Again/Hard/Good/Easy buttons
# ---------------------------------------------------------------------------

def preview_intervals(card: dict) -> dict:
    """
    Returns {1: "1m", 2: "5m", 3: "10m", 4: "4d"} for the four ratings.
    Requires a full card dict (with preset fields) from database.get_card().
    """
    state       = card["state"]
    step_index  = card.get("step_index", 0)
    interval    = card.get("interval", 0)
    ease        = card.get("ease", 2.5)
    l_steps     = _parse_steps(card.get("learning_steps",  "1 10"))
    r_steps     = _parse_steps(card.get("relearning_steps", "10"))
    grad_int    = card.get("graduating_interval", 1)
    easy_int    = card.get("easy_interval",       4)
    min_int     = card.get("minimum_interval",    1)

    if state in ("new", "learning"):
        again = _fmt_min(l_steps[0])
        if step_index == 0 and len(l_steps) > 1:
            hard = _fmt_min((l_steps[0] + l_steps[1]) / 2)
        elif len(l_steps) == 1:
            hard = _fmt_min(l_steps[0] * 1.5)
        else:
            hard = _fmt_min(l_steps[step_index])
        if step_index >= len(l_steps) - 1:
            good = _fmt_day(grad_int)
        else:
            good = _fmt_min(l_steps[step_index + 1])
        easy = _fmt_day(easy_int)

    elif state == "review":
        again = _fmt_min(r_steps[0])
        hard  = _fmt_day(max(min_int, math.floor(interval * 1.2)))
        good  = _fmt_day(max(min_int, math.floor(interval * ease)))
        easy  = _fmt_day(max(min_int, math.floor(interval * ease * 1.3)))

    elif state == "relearn":
        again = _fmt_min(r_steps[0])
        if step_index == 0 and len(r_steps) > 1:
            hard = _fmt_min((r_steps[0] + r_steps[1]) / 2)
        else:
            hard = _fmt_min(r_steps[step_index] * 1.5)
        if step_index >= len(r_steps) - 1:
            good = _fmt_day(max(min_int, interval))
        else:
            good = _fmt_min(r_steps[step_index + 1])
        easy = _fmt_day(max(min_int, interval))

    else:
        again = hard = good = easy = "—"

    return {1: again, 2: hard, 3: good, 4: easy}


def _fmt_min(minutes: float) -> str:
    m = round(minutes)
    if m < 60:
        return f"{m}m"
    return f"{m // 60}h"


def _fmt_day(days: int) -> str:
    if days < 1:
        return "1d"
    if days < 31:
        return f"{days}d"
    months = round(days / 30)
    if months < 12:
        return f"{months}mo"
    return f"{round(days / 365)}y"


# ---------------------------------------------------------------------------
# Pure math helpers — no DB calls
# ---------------------------------------------------------------------------

def _parse_steps(steps_str: str) -> list[int]:
    """Parse step strings to minutes. Supports plain ints ("1 10"), "m" suffix ("1m 10m"), and "d" suffix ("1d" = 1440 min)."""
    result = []
    for s in steps_str.strip().split():
        if s.endswith('d'):
            result.append(int(s[:-1]) * 1440)
        elif s.endswith('m'):
            result.append(int(s[:-1]))
        else:
            result.append(int(s))
    return result


def next_learning_due(steps: list[int], step_index: int) -> str:
    """Returns ISO datetime = now + steps[step_index] minutes."""
    minutes = steps[step_index]
    due = datetime.now() + timedelta(minutes=minutes)
    return due.isoformat(timespec="seconds")


def next_review_due(interval: int) -> str:
    """Returns ISO date = today + interval days."""
    return (date.today() + timedelta(days=interval)).isoformat()


def calc_review(interval: int, ease: float,
                lapses: int, rating: int) -> tuple[int, float, int]:
    """SM-2 variant for review cards.

    Returns (new_interval, new_ease, new_lapses).
    Caller handles state transition to 'relearn' for rating=1.
    """
    if rating == 1:  # Again
        new_ease = max(1.3, ease - 0.20)
        new_interval = max(1, math.floor(interval * 0.5))
        return new_interval, new_ease, lapses + 1

    if rating == 2:  # Hard
        new_ease = max(1.3, ease - 0.15)
        new_interval = math.floor(interval * 1.2)
        return max(1, new_interval), new_ease, lapses

    if rating == 3:  # Good
        new_interval = math.floor(interval * ease)
        return max(1, new_interval), ease, lapses

    # rating == 4: Easy
    new_ease = ease + 0.15
    new_interval = math.floor(interval * new_ease * 1.3)
    return max(1, new_interval), new_ease, lapses


# ---------------------------------------------------------------------------
# Orchestration — calls database.py
# ---------------------------------------------------------------------------

def apply_review(card_id: int, rating: int,
                 user_response: str | None = None) -> dict:
    """Main entry point. Returns updated card dict."""
    card = database.get_card(card_id)
    if not card:
        raise ValueError(f"Card {card_id} not found")

    preset = {
        "learning_steps":      card["learning_steps"],
        "graduating_interval": card["graduating_interval"],
        "easy_interval":       card["easy_interval"],
        "relearning_steps":    card["relearning_steps"],
        "minimum_interval":    card["minimum_interval"],
        "leech_threshold":     card["leech_threshold"],
        "leech_action":        card["leech_action"],
    }

    if card["state"] in ("new", "learning"):
        updated = _handle_learning(card, preset, rating)
    elif card["state"] == "review":
        updated = _handle_review(card, preset, rating)
    elif card["state"] == "relearn":
        updated = _handle_relearn(card, preset, rating)
    else:
        # suspended — shouldn't be reviewed, but handle gracefully
        updated = card

    database.update_card(
        card_id,
        state=updated["state"],
        due=updated["due"],
        step_index=updated["step_index"],
        interval=updated["interval"],
        ease=updated["ease"],
        repetitions=updated["repetitions"],
        lapses=updated["lapses"],
    )
    database.insert_review(card_id, rating, user_response=user_response)
    return database.get_card(card_id)


def _handle_learning(card: dict, preset: dict, rating: int) -> dict:
    """Advance through learning steps, or graduate."""
    steps = _parse_steps(preset["learning_steps"])
    c = dict(card)

    if rating == 4:  # Easy — graduate immediately
        c["state"] = "review"
        c["interval"] = preset["easy_interval"]
        c["due"] = next_review_due(c["interval"])
        c["step_index"] = 0
        c["repetitions"] += 1
        return c

    if rating == 1:  # Again — reset to step 0
        c["state"] = "learning"
        c["step_index"] = 0
        c["due"] = next_learning_due(steps, 0)
        return c

    if rating == 2:  # Hard — stay on current step, slow delay
        c["state"] = "learning"
        idx = c["step_index"]
        if idx == 0 and len(steps) > 1:
            delay = (steps[0] + steps[1]) / 2
        elif len(steps) == 1:
            delay = steps[0] * 1.5
        else:
            delay = steps[idx]
        due = datetime.now() + timedelta(minutes=delay)
        c["due"] = due.isoformat(timespec="seconds")
        return c

    # rating == 3: Good — advance step
    idx = c["step_index"]
    last = len(steps) - 1

    if idx >= last:  # Graduate
        c["state"] = "review"
        c["interval"] = preset["graduating_interval"]
        c["due"] = next_review_due(c["interval"])
        c["step_index"] = 0
        c["repetitions"] += 1
    else:
        c["step_index"] = idx + 1
        c["state"] = "learning"
        c["due"] = next_learning_due(steps, c["step_index"])

    return c


def _handle_review(card: dict, preset: dict, rating: int) -> dict:
    """Apply SM-2. Again → lapse + relearn."""
    c = dict(card)
    new_interval, new_ease, new_lapses = calc_review(
        c["interval"], c["ease"], c["lapses"], rating
    )

    c["ease"] = new_ease
    c["lapses"] = new_lapses

    if rating == 1:  # Lapse → relearn
        relearn_steps = _parse_steps(preset["relearning_steps"])
        c["interval"] = max(preset["minimum_interval"], new_interval)
        c["state"] = "relearn"
        c["step_index"] = 0
        c["due"] = next_learning_due(relearn_steps, 0)
        _check_leech(c, preset)
    else:
        c["interval"] = max(preset["minimum_interval"], new_interval)
        c["state"] = "review"
        c["due"] = next_review_due(c["interval"])
        c["repetitions"] += 1

    return c


def _handle_relearn(card: dict, preset: dict, rating: int) -> dict:
    """Advance through relearning steps. Completion → back to review."""
    steps = _parse_steps(preset["relearning_steps"])
    c = dict(card)

    if rating == 1:  # Again — reset relearn
        c["state"] = "relearn"
        c["step_index"] = 0
        c["due"] = next_learning_due(steps, 0)
        return c

    if rating == 2:  # Hard — repeat current step
        c["state"] = "relearn"
        idx = c["step_index"]
        if idx == 0 and len(steps) > 1:
            delay = (steps[0] + steps[1]) / 2
        elif len(steps) == 1:
            delay = steps[0] * 1.5
        else:
            delay = steps[idx]
        due = datetime.now() + timedelta(minutes=delay)
        c["due"] = due.isoformat(timespec="seconds")
        return c

    idx = c["step_index"]
    last = len(steps) - 1

    if rating == 4 or idx >= last:  # Easy or last step — back to review
        c["state"] = "review"
        c["due"] = next_review_due(c["interval"])
        c["step_index"] = 0
        c["repetitions"] += 1
    else:
        c["step_index"] = idx + 1
        c["state"] = "relearn"
        c["due"] = next_learning_due(steps, c["step_index"])

    return c


def _check_leech(card: dict, preset: dict) -> None:
    """Suspend card if lapses >= leech_threshold."""
    if card["lapses"] >= preset["leech_threshold"]:
        if preset["leech_action"] == "suspend":
            database.suspend_card(card["id"])
            card["state"] = "suspended"
