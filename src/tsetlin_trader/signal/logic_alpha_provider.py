"""Live signal from the logic-alpha-tm research pipeline.

The research package is a walk-forward backtester with no "predict today"
entry point, so this reuses its building blocks the same way one fold of
`run_walk_forward` does: fit the Boolean encoder and model on every day whose
20-day forward label is already known, then predict the latest day. Labels
need 20 days of future data, so the most recent 20 days are naturally left
out of training, which is the same embargo the backtest uses.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from logic_alpha_tm.backtest import _selector
from logic_alpha_tm.config import ResearchConfig
from logic_alpha_tm.data import load_prices_csv
from logic_alpha_tm.features import QuantileBooleanEncoder, build_features
from logic_alpha_tm.strategies import forward_utilities, strategy_labels, strategy_returns

from .base import Signal, SignalProvider

MAX_DATA_AGE_DAYS = 5


def strategy_target_weights(strategy: str, prices: pd.DataFrame) -> tuple[dict[str, float], str]:
    """Which asset each strategy would hold for the next session, using the
    same rules as logic_alpha_tm.strategies.strategy_returns."""
    last = prices.iloc[-1]
    ma20 = prices.SPY.rolling(20).mean().iloc[-1]
    ma100 = prices.SPY.rolling(100).mean().iloc[-1]

    if strategy == "trend":
        if ma20 > ma100:
            return {"SPY": 1.0}, f"SPY 20d MA {ma20:.2f} > 100d MA {ma100:.2f} -> hold SPY"
        return {}, f"SPY 20d MA {ma20:.2f} <= 100d MA {ma100:.2f} -> cash"
    if strategy == "momentum":
        scores = prices[["SPY", "QQQ", "IWM"]].pct_change(60).iloc[-1]
        leader = str(scores.idxmax())
        return {leader: 1.0}, f"60d momentum leader is {leader} ({scores[leader]:+.1%})"
    if strategy == "defensive":
        if last.SPY > ma100:
            return {"SPY": 1.0}, f"SPY {last.SPY:.2f} above 100d MA {ma100:.2f} -> hold SPY"
        return {"TLT": 1.0}, f"SPY {last.SPY:.2f} below 100d MA {ma100:.2f} -> hold TLT"
    if strategy == "cash":
        return {}, "model selected cash"
    raise ValueError(f"Unknown strategy {strategy!r}")


class LogicAlphaProvider(SignalProvider):
    def __init__(
        self,
        prices_csv: str | Path = "data/tiingo-prices.csv",
        model: str = "bernoulli",
        tiingo_token: str | None = None,
        history_start: str = "2008-01-01",
        top_rules: int = 5,
        max_data_age_days: int = MAX_DATA_AGE_DAYS,
        tm_params: dict | None = None,
    ) -> None:
        self.tm_params = tm_params or {}
        self.max_data_age_days = max_data_age_days
        self.prices_csv = Path(prices_csv)
        self.model = model
        self.tiingo_token = tiingo_token
        self.history_start = history_start
        self.top_rules = top_rules
        self.config = ResearchConfig()

    def _load_prices(self) -> pd.DataFrame:
        if self.tiingo_token:
            from logic_alpha_tm.providers import download_tiingo_prices

            adjusted, *_ = download_tiingo_prices(
                self.history_start, date.today().isoformat(), self.tiingo_token
            )
            self.prices_csv.parent.mkdir(parents=True, exist_ok=True)
            adjusted.rename_axis("date").to_csv(self.prices_csv)

        prices = load_prices_csv(self.prices_csv)
        age = (date.today() - prices.index[-1].date()).days
        if age > self.max_data_age_days:
            raise RuntimeError(
                f"Price data is {age} days old (last date {prices.index[-1].date()}). "
                "Refusing to trade on stale data; set TIINGO_API_TOKEN to refresh."
            )
        return prices

    def _explain(self, model, today: pd.DataFrame, strategy: str) -> list[str]:
        """Per-feature contribution to today's decision, for the Bernoulli model.

        The score is a sum of per-feature log-likelihoods, so the winner's lead
        over the runner-up decomposes exactly into one term per Boolean feature.
        """
        if not hasattr(model, "log_p_"):
            return ["(per-feature explanation is only available for the bernoulli model)"]

        values = today.iloc[0].to_numpy(dtype=float)
        scores = model.decision_function(today)[0]
        order = scores.argsort()
        win, runner = int(order[-1]), int(order[-2])
        log_p = model.log_p_
        log_q = np.log1p(-np.exp(log_p))
        contribution = values * (log_p[win] - log_p[runner]) + (1 - values) * (log_q[win] - log_q[runner])
        prior_gap = model.log_prior_[win] - model.log_prior_[runner]
        runner_name = str(model.classes_[runner])

        lines = [f"evidence for '{strategy}' over '{runner_name}': prior {prior_gap:+.2f} + features {contribution.sum():+.2f}"]
        for i in np.argsort(-np.abs(contribution))[: self.top_rules]:
            state = "TRUE" if values[i] else "false"
            lines.append(f"  {model.feature_names_[i]} is {state} -> {contribution[i]:+.2f}")
        return lines

    def _predict_other(self, train_x, train_y, today, cfg):
        model = _selector(self.model, cfg).fit(train_x, train_y)
        predicted, margin = model.predict_with_margin(today)
        strategy = str(predicted[0])
        margin_value = float(margin[0])
        # Pairwise probability of the winner over the runner-up; None when the model has no margin.
        confidence = None if math.isnan(margin_value) else 1.0 / (1.0 + math.exp(-margin_value))
        headline = f"selected strategy '{strategy}' (margin over runner-up {margin_value:+.2f})"
        return strategy, confidence, headline, self._explain(model, today, strategy)

    def _predict_tmu(self, train_x, train_y, today):
        from .tm_model import TsetlinEnsemble

        ensemble = TsetlinEnsemble(**self.tm_params).fit(train_x, train_y)
        sums = ensemble.class_sums(today)
        order = np.argsort(sums)
        strategy = str(ensemble.classes_[order[-1]])
        runner = str(ensemble.classes_[order[-2]])
        margin = float(sums[order[-1]] - sums[order[-2]])
        seed_winners = ensemble.seed_votes(today)
        agree = sum(1 for w in seed_winners if w == strategy)

        headline = (
            f"selected strategy '{strategy}' (vote margin over '{runner}' {margin:+.0f}; "
            f"{agree}/{len(seed_winners)} seeds agree)"
        )
        votes = ", ".join(f"{c} {v:+.0f}" for c, v in zip(ensemble.classes_, sums))
        detail = [f"average votes over {len(seed_winners)} seeds: {votes} (votes are not probabilities)"]

        pro, con = ensemble.explain(today, strategy, self.top_rules)
        detail.append(f"clauses that fired FOR '{strategy}' (strongest first):")
        detail += [f"  {v.vote:+.1f}  IF " + " AND ".join(v.literals) for v in pro] or ["  (none)"]
        detail.append(f"clauses that fired AGAINST '{strategy}':")
        detail += [f"  {v.vote:+.1f}  IF " + " AND ".join(v.literals) for v in con] or ["  (none)"]
        return strategy, None, headline, detail

    def get_current_signal(self) -> Signal:
        cfg = self.config
        prices = self._load_prices()

        features = build_features(prices)
        streams = strategy_returns(prices, cfg.strategy_cost_bps)
        utilities = forward_utilities(streams, cfg.horizon, cfg.lambda_vol, cfg.lambda_drawdown)
        labels = strategy_labels(utilities, cfg.label_dead_zone)

        x = features.drop(columns=["regime"]).dropna()
        train_index = x.index.intersection(labels.dropna().index)
        if len(train_index) < cfg.min_train:
            raise RuntimeError(
                f"Only {len(train_index)} labelled days; need at least {cfg.min_train}. "
                "Download more price history."
            )

        encoder = QuantileBooleanEncoder(cfg.quantiles).fit(x.loc[train_index])
        train_x = encoder.transform(x.loc[train_index])
        train_y = labels.loc[train_index]
        today = encoder.transform(x.iloc[[-1]])

        if self.model == "tmu":
            strategy, confidence, headline, detail = self._predict_tmu(train_x, train_y, today)
        else:
            strategy, confidence, headline, detail = self._predict_other(train_x, train_y, today, cfg)

        weights, holding_note = strategy_target_weights(strategy, prices)
        as_of = x.index[-1].date().isoformat()

        trace = [
            f"model={self.model} trained on {len(train_index)} labelled days "
            f"(through {train_index[-1].date()}), predicting {as_of}",
            f"regime today: {features.regime.iloc[-1]}",
            headline,
            holding_note,
            *detail,
        ]

        return Signal(
            as_of=as_of,
            strategy=strategy,
            target_weights=weights,
            confidence=confidence,
            rule_trace=trace,
        )
