"""Fixed-fraction position sizing plus a max-drawdown circuit breaker.

Risk management is not an afterthought bolted onto a backtest — it's the
gate every signal has to pass through before it becomes an order.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Decision(str, Enum):
    TRADE = "trade"
    HALT = "halt"


@dataclass
class RiskDecision:
    decision: Decision
    sized_weights: dict[str, float]
    reason: str


class RiskManager:
    def __init__(self, max_drawdown_pct: float = 0.15, position_fraction: float = 0.25) -> None:
        if not 0.0 < max_drawdown_pct < 1.0:
            raise ValueError("max_drawdown_pct must be in (0, 1)")
        if not 0.0 < position_fraction <= 1.0:
            raise ValueError("position_fraction must be in (0, 1]")
        self.max_drawdown_pct = max_drawdown_pct
        self.position_fraction = position_fraction
        self._peak_equity: float | None = None

    def _current_drawdown(self, equity: float) -> float:
        if self._peak_equity is None or equity > self._peak_equity:
            self._peak_equity = equity
        if self._peak_equity == 0:
            return 0.0
        return 1.0 - (equity / self._peak_equity)

    def size_order(self, target_weights: dict[str, float], equity: float) -> RiskDecision:
        drawdown = self._current_drawdown(equity)

        if drawdown >= self.max_drawdown_pct:
            return RiskDecision(
                decision=Decision.HALT,
                sized_weights={},
                reason=f"drawdown {drawdown:.2%} >= max_drawdown_pct {self.max_drawdown_pct:.2%}",
            )

        scale = self.position_fraction
        sized = {symbol: round(weight * scale, 6) for symbol, weight in target_weights.items()}

        return RiskDecision(
            decision=Decision.TRADE,
            sized_weights=sized,
            reason=f"drawdown {drawdown:.2%} within limit; scaled by position_fraction={scale}",
        )
