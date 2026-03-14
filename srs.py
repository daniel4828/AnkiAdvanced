"""SM-2 with Anki-style learning steps and three-queue scheduling."""
from datetime import datetime, timedelta, date, timezone

MIN_EASE = 1.3
EASE_DELTA = {1: -0.2, 2: -0.15, 3: 0.0, 4: 0.15}

LEARN_STEPS_MINUTES  = [1, 10]   # new card learning steps
RELEARN_STEPS_MINUTES = [10]     # lapse relearning steps
GRADUATING_INTERVAL  = 1         # days after completing learn steps
EASY_INTERVAL        = 4         # days for Easy on any learning card


def _due_in(minutes: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")


def next_review(rep: int, interval: int, ease: float, rating: int,
                state: str, learning_step: int = 0) -> tuple:
    """
    Returns (new_rep, new_interval, new_ease, new_state, new_learning_step, learning_due)
    learning_due: ISO datetime string for learning cards, None for review cards
    interval: days (only meaningful for review cards)
    """
    new_ease = max(MIN_EASE, ease + EASE_DELTA[rating])
    is_learning = state in ("new", "learning")
    steps = LEARN_STEPS_MINUTES if is_learning else RELEARN_STEPS_MINUTES

    if is_learning or state == "learning":
        if rating == 1:   # Again → restart at step 0
            return 0, 0, new_ease, "learning", 0, _due_in(LEARN_STEPS_MINUTES[0])
        elif rating == 2: # Hard → repeat current step
            s = min(learning_step, len(steps) - 1)
            return 0, 0, new_ease, "learning", s, _due_in(steps[s])
        elif rating == 3: # Good → next step, or graduate
            next_step = learning_step + 1
            if next_step >= len(LEARN_STEPS_MINUTES):
                return 1, GRADUATING_INTERVAL, new_ease, "review", 0, None
            return 0, 0, new_ease, "learning", next_step, _due_in(LEARN_STEPS_MINUTES[next_step])
        else:             # Easy → graduate immediately
            return 1, EASY_INTERVAL, new_ease, "review", 0, None
    else:
        # Review card
        if rating == 1:   # Lapse → relearn
            new_interval = max(1, round(interval * 0.5))
            return 0, new_interval, new_ease, "learning", 0, _due_in(RELEARN_STEPS_MINUTES[0])
        elif rating == 2: # Hard
            new_interval = max(round(interval * 1.2), interval + 1)
            return rep, new_interval, new_ease, "review", 0, None
        elif rating == 3: # Good
            new_interval = max(round(interval * new_ease), interval + 1)
            return rep + 1, new_interval, new_ease, "review", 0, None
        else:             # Easy
            new_interval = max(round(interval * new_ease * 1.3), interval + 2)
            return rep + 1, new_interval, new_ease, "review", 0, None


def due_date(interval: int, from_date: date = None) -> str:
    base = from_date or date.today()
    return (base + timedelta(days=interval)).isoformat()


def preview_intervals(state: str, interval: int, ease: float, learning_step: int = 0) -> dict:
    """Display labels for the 4 rating buttons."""
    def fmt_days(d):
        if d == 1: return "1d"
        if d < 30: return f"{d}d"
        if d < 365: return f"{round(d/30)}mo"
        return f"{round(d/365,1)}y"

    is_learning = state in ("new", "learning")

    if is_learning:
        # Hard: repeat current step; Good: next step or graduate
        hard_min = LEARN_STEPS_MINUTES[min(learning_step, len(LEARN_STEPS_MINUTES)-1)]
        next_step = learning_step + 1
        if next_step >= len(LEARN_STEPS_MINUTES):
            good_label = fmt_days(GRADUATING_INTERVAL)
        else:
            good_label = f"{LEARN_STEPS_MINUTES[next_step]}m"
        return {
            1: f"{LEARN_STEPS_MINUTES[0]}m",
            2: f"{hard_min}m",
            3: good_label,
            4: fmt_days(EASY_INTERVAL),
        }
    else:
        hard = max(round(interval * 1.2), interval + 1)
        good = max(round(interval * ease), interval + 1)
        easy = max(round(interval * ease * 1.3), interval + 2)
        return {1: "10m", 2: fmt_days(hard), 3: fmt_days(good), 4: fmt_days(easy)}
