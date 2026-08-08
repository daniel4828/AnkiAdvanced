#!/usr/bin/env python3
"""Train personal FSRS-5 weights from real review history (issue #629).

The weights Anki ships are an average over many users. For Daniel they are
systematically optimistic: measured forgetting sits at 20–31% while the target
is 5–10%, and the forgetting-vs-interval curve is flat rather than decaying —
the signature of a mis-calibrated memory model rather than of intervals that
merely grew too long.

What this does: replay every card's review history through the exact DSR model
in `fsrs.py`, compare the retrievability the model predicted against what
actually happened, and search for the 19 weights that minimise log loss.

Read-only by default. It prints weights; a human writes them to the preset.

    python scripts/fsrs_optimize.py --db data/srs.db
    python scripts/fsrs_optimize.py --db data/srs.db --by-category
"""

import argparse
import math
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fsrs

# Search bounds per weight, mirroring the ranges upstream FSRS uses when it
# optimises. Without these the search happily walks into regions that fit the
# history but produce absurd intervals on new cards.
BOUNDS = [
    (0.01, 100.0), (0.01, 100.0), (0.01, 100.0), (0.01, 100.0),  # w0-3  init stability
    (1.0, 10.0), (0.001, 4.0),                                    # w4-5  init difficulty
    (0.001, 4.0), (0.001, 0.75),                                  # w6-7  difficulty update
    (0.0, 4.5), (0.0, 0.8), (0.001, 3.5),                         # w8-10 recall stability
    (0.001, 5.0), (0.001, 0.25), (0.001, 0.9), (0.0, 4.0),        # w11-14 forget stability
    (0.0, 1.0), (1.0, 6.0),                                       # w15-16 hard penalty / easy bonus
    (0.0, 2.0), (0.0, 2.0),                                       # w17-18 short-term
]

# Predictions are clipped before the log so a single confident miss cannot
# dominate the objective with an infinite loss.
_EPS = 1e-6


def _parse_ts(ts: str) -> datetime:
    """Parse the two timestamp shapes review_log holds (with or without 'T')."""
    return datetime.fromisoformat(ts.replace("T", " ")[:19])


def load_sequences(db_path: str, category: str | None = None) -> list[list[tuple[float, int]]]:
    """Rebuild each card's review history as a list of (elapsed_days, rating).

    elapsed_days is measured from the previous review of that card; the first
    review of a card gets 0.0 and seeds the initial state instead of being
    scored. Cards with a single review carry no predictive signal and are
    dropped.
    """
    con = sqlite3.connect(db_path)
    sql = """
        SELECT r.card_id, r.reviewed_at, r.rating
        FROM review_log r JOIN cards c ON c.id = r.card_id
        WHERE r.rating BETWEEN 1 AND 4
    """
    params: list = []
    if category:
        sql += " AND c.category = ?"
        params.append(category)
    sql += " ORDER BY r.card_id, r.reviewed_at"
    rows = con.execute(sql, params).fetchall()
    con.close()

    sequences: list[list[tuple[float, int]]] = []
    current: list[tuple[float, int]] = []
    prev_card = None
    prev_time = None

    for card_id, ts, rating in rows:
        try:
            when = _parse_ts(ts)
        except ValueError:
            continue
        if card_id != prev_card:
            if len(current) > 1:
                sequences.append(current)
            current = [(0.0, rating)]
            prev_card = card_id
        else:
            elapsed = (when - prev_time).total_seconds() / 86400.0
            current.append((max(0.0, elapsed), rating))
        prev_time = when

    if len(current) > 1:
        sequences.append(current)
    return sequences


def evaluate(weights: list[float], sequences: list[list[tuple[float, int]]]) -> tuple[float, int]:
    """Replay every sequence and return (mean log loss, scored review count).

    Only reviews separated by at least a day are scored. Same-day repeats are
    replayed through the short-term formula so state stays faithful, but their
    retrievability prediction is not a claim about long-term memory and
    scoring them would drown out the signal we actually care about.
    """
    total = 0.0
    n = 0

    for seq in sequences:
        _, first_rating = seq[0]
        stability = fsrs.init_stability(weights, first_rating)
        difficulty = fsrs.init_difficulty(weights, first_rating)

        for elapsed, rating in seq[1:]:
            if elapsed < 1.0:
                stability = fsrs.next_short_term_stability(weights, stability, rating)
                continue

            r = fsrs.retrievability(elapsed, stability)
            p = min(1.0 - _EPS, max(_EPS, r))
            total += -math.log(p) if rating > 1 else -math.log(1.0 - p)
            n += 1

            stability, difficulty = fsrs.review_state(
                weights, difficulty, stability, elapsed, rating
            )

    return (total / n if n else float("inf")), n


def calibration(weights: list[float], sequences: list[list[tuple[float, int]]]) -> list[tuple]:
    """Bucket scored reviews by predicted R and report predicted vs actual.

    This is what tells you whether the model is honest. A well-calibrated model
    puts actual recall right on top of predicted recall in every bucket.
    """
    edges = [0.0, 0.5, 0.7, 0.8, 0.9, 0.95, 1.0]
    buckets = [[0, 0, 0.0] for _ in range(len(edges) - 1)]  # [n, recalled, sum_pred]

    for seq in sequences:
        _, first_rating = seq[0]
        stability = fsrs.init_stability(weights, first_rating)
        difficulty = fsrs.init_difficulty(weights, first_rating)

        for elapsed, rating in seq[1:]:
            if elapsed < 1.0:
                stability = fsrs.next_short_term_stability(weights, stability, rating)
                continue
            r = fsrs.retrievability(elapsed, stability)
            for i in range(len(edges) - 1):
                if edges[i] <= r < edges[i + 1] or (i == len(edges) - 2 and r >= edges[i + 1]):
                    buckets[i][0] += 1
                    buckets[i][1] += 1 if rating > 1 else 0
                    buckets[i][2] += r
                    break
            stability, difficulty = fsrs.review_state(
                weights, difficulty, stability, elapsed, rating
            )

    out = []
    for i, (n, recalled, sum_pred) in enumerate(buckets):
        if n:
            out.append((f"{edges[i]:.2f}-{edges[i+1]:.2f}", n, sum_pred / n, recalled / n))
    return out


def optimize(sequences: list[list[tuple[float, int]]], rounds: int = 6,
             verbose: bool = True) -> list[float]:
    """Coordinate descent over the 19 weights, golden-section on each axis.

    Chosen over gradient descent deliberately: no learning rate to tune, no
    numerical-gradient noise, and it cannot diverge — it only ever accepts a
    move that lowered the loss. Slower per round, but this runs once.
    """
    weights = list(fsrs.DEFAULT_WEIGHTS)
    best, _ = evaluate(weights, sequences)

    for rnd in range(rounds):
        improved = False
        for i in range(len(weights)):
            lo, hi = BOUNDS[i]
            original = weights[i]

            # Golden-section search on axis i.
            phi = (math.sqrt(5) - 1) / 2
            a, b = lo, hi
            for _ in range(12):
                x1, x2 = b - phi * (b - a), a + phi * (b - a)
                weights[i] = x1
                f1, _ = evaluate(weights, sequences)
                weights[i] = x2
                f2, _ = evaluate(weights, sequences)
                if f1 < f2:
                    b = x2
                else:
                    a = x1

            weights[i] = (a + b) / 2
            candidate, _ = evaluate(weights, sequences)
            if candidate < best - 1e-9:
                best = candidate
                improved = True
            else:
                weights[i] = original

        if verbose:
            print(f"  轮 {rnd + 1}/{rounds}: log loss = {best:.5f}")
        if not improved:
            if verbose:
                print("  已收敛，提前结束。")
            break

    return weights


def _split(sequences: list, holdout: float) -> tuple[list, list]:
    """Split by card, not by review — a card's reviews must not straddle the
    boundary or the model gets to peek at the state it is being tested on."""
    cut = int(len(sequences) * (1 - holdout))
    return sequences[:cut], sequences[cut:]


def _report(label: str, sequences: list, trained: list[float]) -> None:
    base_loss, n = evaluate(fsrs.DEFAULT_WEIGHTS, sequences)
    new_loss, _ = evaluate(trained, sequences)
    delta = (base_loss - new_loss) / base_loss * 100 if base_loss else 0.0
    print(f"\n{label}（{n} 条计分复习）")
    print(f"  默认权重 log loss : {base_loss:.5f}")
    print(f"  训练后   log loss : {new_loss:.5f}  ({delta:+.1f}%)")

    print(f"\n  校准表（预测 R vs 实际记住率）")
    print(f"  {'R 区间':<12} {'样本':>6} {'预测':>8} {'实际':>8}  {'默认→实际':>10}")
    base_cal = {b[0]: b for b in calibration(fsrs.DEFAULT_WEIGHTS, sequences)}
    for bucket, n_b, pred, actual in calibration(trained, sequences):
        old = base_cal.get(bucket)
        old_txt = f"{old[2]:.3f}→{old[3]:.3f}" if old else "—"
        print(f"  {bucket:<12} {n_b:>6} {pred:>8.3f} {actual:>8.3f}  {old_txt:>10}")


def main() -> int:
    ap = argparse.ArgumentParser(description="用真实复习历史训练个人 FSRS 权重（#629）")
    ap.add_argument("--db", default=os.environ.get("DB_PATH", "data/srs.db"))
    ap.add_argument("--category", help="只用某个类别的历史（creating/listening/reading）")
    ap.add_argument("--by-category", action="store_true",
                    help="对 creating 和 listening 分别训练并对比")
    ap.add_argument("--holdout", type=float, default=0.2,
                    help="按卡片切出的验证集比例（默认 0.2；设 0 则不切）")
    ap.add_argument("--rounds", type=int, default=6, help="坐标下降轮数（默认 6）")
    ap.add_argument("--write", action="store_true",
                    help="把训练结果写回 deck_presets.fsrs_weights（默认只打印）")
    ap.add_argument("--preset-id", type=int, default=2, help="--write 时要更新的 preset（默认 2）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"找不到数据库：{args.db}", file=sys.stderr)
        return 1

    targets = ["creating", "listening"] if args.by_category else [args.category]

    for cat in targets:
        label = cat or "全部类别"
        print(f"\n{'=' * 60}\n训练目标：{label}\n{'=' * 60}")

        sequences = load_sequences(args.db, cat)
        if not sequences:
            print("没有可用的复习序列，跳过。")
            continue
        print(f"载入 {len(sequences)} 张卡的复习序列。")

        train, test = _split(sequences, args.holdout) if args.holdout > 0 else (sequences, [])
        print(f"训练集 {len(train)} 张卡 / 验证集 {len(test)} 张卡\n开始坐标下降……")

        trained = optimize(train, rounds=args.rounds)

        _report("【训练集】", train, trained)
        if test:
            _report("【验证集】——只信这个", test, trained)

        print(f"\n  训练出的权重：")
        print("  " + " ".join(f"{w:.5f}" for w in trained))

        if args.write:
            if not cat:
                con = sqlite3.connect(args.db)
                con.execute("UPDATE deck_presets SET fsrs_weights=? WHERE id=?",
                            (" ".join(f"{w:.5f}" for w in trained), args.preset_id))
                con.commit()
                con.close()
                print(f"  已写入 preset {args.preset_id}。")
            else:
                print("  --write 不支持按类别训练的结果（preset 是牌组级的，不分类别）。")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
