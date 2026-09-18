import numpy as np
import pandas as pd
import pytest

pytest.importorskip("tmu")

from tsetlin_trader.signal.tm_model import TsetlinEnsemble  # noqa: E402

SMALL = dict(clauses=60, threshold=20, epochs=5, seeds=(1, 2))


@pytest.fixture(scope="module")
def data():
    rng = np.random.default_rng(3)
    x = pd.DataFrame(rng.integers(0, 2, (400, 10)), columns=[f"f{i}" for i in range(10)])
    y = pd.Series(np.where(x.f0 & x.f1, "a", np.where(x.f2 & (1 - x.f3), "b", "c")))
    return x, y


@pytest.fixture(scope="module")
def fitted(data):
    x, y = data
    return TsetlinEnsemble(**SMALL).fit(x, y)


def winner(ensemble, row):
    return str(ensemble.classes_[int(np.argmax(ensemble.class_sums(row)))])


def test_learns_a_simple_boolean_rule(fitted, data):
    x, y = data
    hits = sum(winner(fitted, x.iloc[[i]]) == y.iloc[i] for i in range(0, 400, 8))
    assert hits / 50 > 0.8


def test_same_seeds_give_identical_votes(data):
    x, y = data
    first = TsetlinEnsemble(**SMALL).fit(x, y).class_sums(x.iloc[[0]])
    second = TsetlinEnsemble(**SMALL).fit(x, y).class_sums(x.iloc[[0]])
    assert np.array_equal(first, second)


def test_fired_clauses_add_up_to_the_model_votes(fitted, data):
    x, _ = data
    for i in (0, 5, 17):
        row = x.iloc[[i]]
        sums = fitted.class_sums(row)
        for ci, name in enumerate(fitted.classes_):
            assert fitted.total_fired_vote(row, str(name)) == pytest.approx(sums[ci])


def test_every_reported_clause_is_true_on_the_row(fitted, data):
    """A clause is an AND of literals, so each one listed must hold on the row.
    This fails if the positive/negated literal numbering were read backwards."""
    x, _ = data
    checked = 0
    for i in (0, 5, 17, 42):
        row = x.iloc[i]
        for name in fitted.classes_:
            pro, con = fitted.explain(x.iloc[[i]], str(name), top_n=5)
            for clause in pro + con:
                for literal in clause.literals:
                    if literal.startswith("NOT "):
                        assert row[literal[4:]] == 0
                    else:
                        assert row[literal] == 1
                    checked += 1
    assert checked > 0


def test_pro_clauses_vote_for_and_con_clauses_vote_against(fitted, data):
    x, _ = data
    pro, con = fitted.explain(x.iloc[[0]], str(fitted.classes_[0]), top_n=5)
    assert all(c.vote > 0 for c in pro)
    assert all(c.vote < 0 for c in con)


def test_seed_votes_report_one_winner_per_seed(fitted, data):
    x, _ = data
    votes = fitted.seed_votes(x.iloc[[0]])
    assert len(votes) == len(SMALL["seeds"])
    assert set(votes) <= set(map(str, fitted.classes_))
