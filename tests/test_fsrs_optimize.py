"""Tests for scripts/fsrs_optimize.py (issue #629).

The optimiser is only trustworthy if it can recover a memory model we already
know the answer to. So the core test generates synthetic review histories from
a known set of weights and checks that training on them beats the FSRS
defaults — if it cannot do that on clean data, its output on real data is
meaningless.
"""

import math
import os
import random
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import fsrs
import fsrs_optimize as opt


# --- sequence loading -------------------------------------------------------

def _make_db(path, rows):
    """rows: (card_id, category, reviewed_at, rating)"""
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE cards (id INTEGER PRIMARY KEY, category TEXT)")
    con.execute("CREATE TABLE review_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "card_id INTEGER, reviewed_at TEXT, rating INTEGER)")
    for card_id, category in {(r[0], r[1]) for r in rows}:
        con.execute("INSERT OR IGNORE INTO cards (id, category) VALUES (?,?)", (card_id, category))
    for card_id, _, ts, rating in rows:
        con.execute("INSERT INTO review_log (card_id, reviewed_at, rating) VALUES (?,?,?)",
                    (card_id, ts, rating))
    con.commit()
    con.close()


def test_load_sequences_computes_elapsed_days(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db, [
        (1, "creating", "2026-01-01 10:00:00", 3),
        (1, "creating", "2026-01-04 10:00:00", 3),
        (1, "creating", "2026-01-14 10:00:00", 1),
    ])
    seqs = opt.load_sequences(db)
    assert len(seqs) == 1
    elapsed = [e for e, _ in seqs[0]]
    assert elapsed[0] == 0.0
    assert elapsed[1] == pytest.approx(3.0)
    assert elapsed[2] == pytest.approx(10.0)


def test_load_sequences_drops_single_review_cards(tmp_path):
    """One review carries no prediction to score — it only seeds state."""
    db = str(tmp_path / "t.db")
    _make_db(db, [
        (1, "creating", "2026-01-01 10:00:00", 3),
        (2, "creating", "2026-01-01 10:00:00", 3),
        (2, "creating", "2026-01-05 10:00:00", 3),
    ])
    assert len(opt.load_sequences(db)) == 1


def test_load_sequences_filters_by_category(tmp_path):
    db = str(tmp_path / "t.db")
    _make_db(db, [
        (1, "creating", "2026-01-01 10:00:00", 3),
        (1, "creating", "2026-01-05 10:00:00", 3),
        (2, "listening", "2026-01-01 10:00:00", 3),
        (2, "listening", "2026-01-05 10:00:00", 3),
    ])
    assert len(opt.load_sequences(db, "creating")) == 1
    assert len(opt.load_sequences(db, "listening")) == 1
    assert len(opt.load_sequences(db)) == 2


def test_load_sequences_handles_iso_t_separator(tmp_path):
    """review_log holds both 'YYYY-MM-DD HH:MM:SS' and ISO 'T' forms."""
    db = str(tmp_path / "t.db")
    _make_db(db, [
        (1, "creating", "2026-01-01T10:00:00", 3),
        (1, "creating", "2026-01-03T10:00:00", 3),
    ])
    seqs = opt.load_sequences(db)
    assert seqs[0][1][0] == pytest.approx(2.0)


# --- evaluation -------------------------------------------------------------

def test_evaluate_scores_only_cross_day_reviews():
    """Same-day repeats update state but must not enter the loss."""
    seq = [[(0.0, 3), (0.2, 3), (0.5, 3)]]
    _, n = opt.evaluate(fsrs.DEFAULT_WEIGHTS, seq)
    assert n == 0

    seq = [[(0.0, 3), (0.2, 3), (5.0, 3)]]
    _, n = opt.evaluate(fsrs.DEFAULT_WEIGHTS, seq)
    assert n == 1


def test_evaluate_penalises_confidently_wrong_predictions():
    """A lapse one day after a high-stability review must cost more than a
    lapse long after it, where the model already expected forgetting."""
    soon = [[(0.0, 4), (1.0, 1)]]
    late = [[(0.0, 4), (400.0, 1)]]
    loss_soon, _ = opt.evaluate(fsrs.DEFAULT_WEIGHTS, soon)
    loss_late, _ = opt.evaluate(fsrs.DEFAULT_WEIGHTS, late)
    assert loss_soon > loss_late


def test_evaluate_is_finite_on_extreme_predictions():
    """Clipping must keep log loss finite even when R saturates at 0 or 1."""
    seq = [[(0.0, 1), (10000.0, 3)], [(0.0, 4), (0.0001, 1)]]
    loss, n = opt.evaluate(fsrs.DEFAULT_WEIGHTS, seq)
    assert n >= 1
    assert math.isfinite(loss)


# --- optimiser --------------------------------------------------------------

def _simulate(weights, n_cards=300, seed=7):
    """Generate review histories from a known memory model.

    Reviews are sampled from the true recall probability, so the data really
    is described by `weights` and a correct optimiser should find its way back
    toward them.
    """
    rng = random.Random(seed)
    sequences = []
    for _ in range(n_cards):
        first = rng.choice([3, 3, 3, 4])
        stability = fsrs.init_stability(weights, first)
        difficulty = fsrs.init_difficulty(weights, first)
        seq = [(0.0, first)]
        for _ in range(rng.randint(4, 9)):
            elapsed = float(max(1, round(fsrs.next_interval(stability, 0.9))))
            r = fsrs.retrievability(elapsed, stability)
            rating = 3 if rng.random() < r else 1
            seq.append((elapsed, rating))
            stability, difficulty = fsrs.review_state(
                weights, difficulty, stability, elapsed, rating
            )
        sequences.append(seq)
    return sequences


def test_optimizer_beats_defaults_on_data_from_other_weights():
    """The headline claim: on data generated by a memory model that is *not*
    the FSRS default, training must produce weights that fit it better."""
    true_w = list(fsrs.DEFAULT_WEIGHTS)
    true_w[2] = 1.2      # much weaker initial stability for Good
    true_w[8] = 0.9      # slower stability growth on recall
    sequences = _simulate(true_w)

    baseline, _ = opt.evaluate(fsrs.DEFAULT_WEIGHTS, sequences)
    trained = opt.optimize(sequences, rounds=2, verbose=False)
    after, _ = opt.evaluate(trained, sequences)

    assert after < baseline
    truth, _ = opt.evaluate(true_w, sequences)
    # Should close a meaningful share of the gap to the generating model.
    assert after < baseline - 0.25 * (baseline - truth)


def test_optimizer_respects_bounds():
    sequences = _simulate(fsrs.DEFAULT_WEIGHTS, n_cards=60)
    trained = opt.optimize(sequences, rounds=1, verbose=False)
    assert len(trained) == len(fsrs.DEFAULT_WEIGHTS)
    for w, (lo, hi) in zip(trained, opt.BOUNDS):
        assert lo <= w <= hi


def test_optimizer_never_returns_worse_than_defaults():
    """Coordinate descent only accepts improving moves, so its result can
    never be worse than the starting point it was seeded with."""
    sequences = _simulate(fsrs.DEFAULT_WEIGHTS, n_cards=60, seed=11)
    baseline, _ = opt.evaluate(fsrs.DEFAULT_WEIGHTS, sequences)
    trained = opt.optimize(sequences, rounds=1, verbose=False)
    after, _ = opt.evaluate(trained, sequences)
    assert after <= baseline + 1e-9


# --- calibration & splitting ------------------------------------------------

def test_calibration_reports_predicted_and_actual():
    sequences = _simulate(fsrs.DEFAULT_WEIGHTS, n_cards=120)
    table = opt.calibration(fsrs.DEFAULT_WEIGHTS, sequences)
    assert table
    for _, n, pred, actual in table:
        assert n > 0
        assert 0.0 <= pred <= 1.0
        assert 0.0 <= actual <= 1.0


def test_split_keeps_each_card_whole():
    """A card's reviews must not straddle the train/test boundary, or the
    model gets to peek at the state it is being evaluated on."""
    sequences = [[(0.0, 3), (float(i), 3)] for i in range(1, 11)]
    train, test = opt._split(sequences, 0.2)
    assert len(train) == 8
    assert len(test) == 2
    assert not [s for s in train if s in test]
