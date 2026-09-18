"""A seeded Tsetlin Machine ensemble that can explain its own votes.

The research repo's TMUSelector is unseeded and returns no clauses, which is
fine for research but not for a bot: identical data could give a different
signal on each run, and nothing would say why. This wrapper trains the same
model family (same size and settings as TMUSelector's defaults) with fixed
seeds, averages their votes, and reads the fired clauses back out.

The explanation reconciles exactly: a class's vote is the dot product of its
clause weights and clause outputs, so summing every fired clause's weight
gives the model's own total (verified in tests/test_tm_model.py).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd

# tmu logs a noisy (harmless) traceback when the optional GPU library is absent.
logging.getLogger("tmu").setLevel(logging.CRITICAL)

DEFAULT_SEEDS = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class ClauseVote:
    vote: float
    literals: tuple[str, ...]


class TsetlinEnsemble:
    def __init__(
        self,
        clauses: int = 1000,
        threshold: int = 100,
        specificity: float = 5.0,
        epochs: int = 20,
        seeds: tuple[int, ...] = DEFAULT_SEEDS,
    ) -> None:
        self.clauses = clauses
        self.threshold = threshold
        self.specificity = specificity
        self.epochs = epochs
        self.seeds = seeds
        self.classes_: np.ndarray | None = None
        self.feature_names_: list[str] = []
        self._models: list = []

    def fit(self, x: pd.DataFrame, y: pd.Series) -> "TsetlinEnsemble":
        try:
            from tmu.models.classification.vanilla_classifier import TMClassifier
        except ImportError as exc:
            raise RuntimeError(
                "The Tsetlin Machine is optional. Install it with: pip install -e \".[tm]\""
            ) from exc

        self.feature_names_ = list(x.columns)
        self.classes_, encoded = np.unique(y, return_inverse=True)
        features = x.to_numpy(dtype=np.uint32)
        labels = encoded.astype(np.uint32)

        self._models = []
        for seed in self.seeds:
            model = TMClassifier(
                self.clauses, self.threshold, self.specificity,
                platform="CPU", weighted_clauses=True, seed=seed,
            )
            for _ in range(self.epochs):
                model.fit(features, labels)
            self._models.append(model)
        return self

    def _outputs(self, model, row: np.ndarray, class_index: int) -> tuple[np.ndarray, np.ndarray]:
        bank = model.clause_banks[class_index]
        encoded = bank.prepare_X(row)
        weights = np.asarray(model.weight_banks[class_index].get_weights())
        outputs = np.asarray(bank.calculate_clause_outputs_predict(encoded, 0))
        return weights, outputs

    def class_sums(self, x_row: pd.DataFrame) -> np.ndarray:
        """Mean vote per class across seeds for a single-row frame."""
        row = x_row.to_numpy(dtype=np.uint32)
        totals = np.zeros(len(self.classes_))
        for model in self._models:
            for ci in range(len(self.classes_)):
                weights, outputs = self._outputs(model, row, ci)
                totals[ci] += float(np.dot(weights, outputs))
        return totals / len(self._models)

    def seed_votes(self, x_row: pd.DataFrame) -> list[str]:
        """Each seed's own winning class, to show how much the seeds agree."""
        row = x_row.to_numpy(dtype=np.uint32)
        winners = []
        for model in self._models:
            sums = [float(np.dot(*self._outputs(model, row, ci))) for ci in range(len(self.classes_))]
            winners.append(str(self.classes_[int(np.argmax(sums))]))
        return winners

    def _literals(self, model, class_index: int, clause: int) -> tuple[str, ...]:
        n = len(self.feature_names_)
        bank = model.clause_banks[class_index]
        names = []
        for ta in range(2 * n):
            if bank.get_ta_action(clause, ta):
                names.append(self.feature_names_[ta] if ta < n else f"NOT {self.feature_names_[ta - n]}")
        return tuple(names)

    def explain(self, x_row: pd.DataFrame, class_name: str, top_n: int = 5) -> tuple[list[ClauseVote], list[ClauseVote]]:
        """Fired clauses for `class_name`: the strongest for it and the strongest against it.

        Votes are averaged over seeds so they sum to the same total as class_sums().
        """
        ci = int(np.where(self.classes_ == class_name)[0][0])
        row = x_row.to_numpy(dtype=np.uint32)
        fired = []
        for model in self._models:
            weights, outputs = self._outputs(model, row, ci)
            for clause in np.nonzero(outputs)[0]:
                fired.append((float(weights[clause]) / len(self._models), model, int(clause)))

        def build(items):
            return [ClauseVote(vote, self._literals(model, ci, clause)) for vote, model, clause in items]

        pro = sorted((f for f in fired if f[0] > 0), key=lambda f: -f[0])[:top_n]
        con = sorted((f for f in fired if f[0] < 0), key=lambda f: f[0])[:top_n]
        return build(pro), build(con)

    def total_fired_vote(self, x_row: pd.DataFrame, class_name: str) -> float:
        """Sum of every fired clause's vote for a class; equals class_sums() for that class."""
        ci = int(np.where(self.classes_ == class_name)[0][0])
        row = x_row.to_numpy(dtype=np.uint32)
        total = 0.0
        for model in self._models:
            weights, outputs = self._outputs(model, row, ci)
            total += float(weights[np.nonzero(outputs)[0]].sum())
        return total / len(self._models)
