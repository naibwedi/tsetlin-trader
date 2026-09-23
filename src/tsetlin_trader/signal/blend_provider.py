"""Deterministic three-strategy blend used as the paper account controller."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from logic_alpha_tm.data import load_prices_csv

from ..portfolios import blend_weights
from .base import Signal, SignalProvider
from .logic_alpha_provider import MAX_DATA_AGE_DAYS


class BlendSignalProvider(SignalProvider):
    """Equal blend of trend, momentum, and defensive strategy sleeves.

    It has no fitted statistical model and therefore no probability. The
    component rules are the same deterministic rules used by the research
    comparison portfolio.
    """

    def __init__(self, prices_csv: str | Path = "data/tiingo-prices.csv",
                 tiingo_token: str | None = None, history_start: str = "2008-01-01",
                 max_data_age_days: int = MAX_DATA_AGE_DAYS) -> None:
        self.prices_csv = Path(prices_csv)
        self.tiingo_token = tiingo_token
        self.history_start = history_start
        self.max_data_age_days = max_data_age_days

    def _prices(self):
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
            raise RuntimeError(f"Price data is {age} days old; refusing a stale blend signal")
        return prices

    def get_current_signal(self) -> Signal:
        from .logic_alpha_provider import strategy_target_weights
        prices = self._prices()
        weights = blend_weights(prices)
        notes = []
        for strategy in ("trend", "momentum", "defensive"):
            sleeve, reason = strategy_target_weights(strategy, prices)
            notes.append(f"{strategy} sleeve (1/3): {reason}; target={sleeve or {'cash': 1.0}}")
        notes.append(f"combined blend target before account risk scaling: {weights}")
        return Signal(as_of=prices.index[-1].date().isoformat(), strategy="blend",
                      target_weights=weights, confidence=None, rule_trace=notes)
