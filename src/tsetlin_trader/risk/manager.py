"""Fixed-fraction position sizing plus a persistent max-drawdown circuit breaker.

Risk management is not an afterthought bolted onto a backtest — it's the
gate every signal has to pass through before it becomes an order.

The breaker is sticky: once tripped it stays tripped until a human resets it,
because an automatic restart after a large loss is how small losses become
big ones. A HALT means "go to cash and stop", not "stop trading but keep
holding the losing positions".
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class Decision(str, Enum):
    TRADE = "trade"
    HALT = "halt"


@dataclass
class RiskDecision:
    decision: Decision
    sized_weights: dict[str, float]
    reason: str


class RiskManager:
    def __init__(
        self,
        max_drawdown_pct: float = 0.15,
        position_fraction: float = 0.25,
        peak_equity: float | None = None,
        halted: bool = False,
    ) -> None:
        if not 0.0 < max_drawdown_pct < 1.0:
            raise ValueError("max_drawdown_pct must be in (0, 1)")
        if not 0.0 < position_fraction <= 1.0:
            raise ValueError("position_fraction must be in (0, 1]")
        self.max_drawdown_pct = max_drawdown_pct
        self.position_fraction = position_fraction
        self.peak_equity = peak_equity
        self.halted = halted

    @classmethod
    def from_state_file(cls, path: Path, **kwargs) -> "RiskManager":
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            return cls(peak_equity=state.get("peak_equity"), halted=state.get("halted", False), **kwargs)
        return cls(**kwargs)

    def save_state(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"peak_equity": self.peak_equity, "halted": self.halted}, indent=2),
            encoding="utf-8",
        )

    def _current_drawdown(self, equity: float) -> float:
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity
        if self.peak_equity == 0:
            return 0.0
        return 1.0 - (equity / self.peak_equity)

    def size_order(self, target_weights: dict[str, float], equity: float) -> RiskDecision:
        drawdown = self._current_drawdown(equity)

        if self.halted:
            return RiskDecision(
                Decision.HALT, {},
                f"circuit breaker previously tripped (drawdown now {drawdown:.2%}); "
                "delete results/state.json to reset",
            )

        if drawdown >= self.max_drawdown_pct:
            self.halted = True
            return RiskDecision(
                Decision.HALT, {},
                f"drawdown {drawdown:.2%} >= max_drawdown_pct {self.max_drawdown_pct:.2%}; liquidating to cash",
            )

        scale = self.position_fraction
        sized = {symbol: round(weight * scale, 6) for symbol, weight in target_weights.items()}
        return RiskDecision(
            Decision.TRADE, sized,
            f"drawdown {drawdown:.2%} within limit; scaled by position_fraction={scale}",
        )
