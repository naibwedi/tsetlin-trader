"""The seam between signal generation (research) and execution (this repo).

Anything that can produce a `Signal` — a mock, or eventually the real
Tsetlin Machine model from logic-alpha-tm — can be plugged into
`run_cycle.py` without changing risk, broker, or logging code.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timezone

from pydantic import BaseModel, Field

UNIVERSE = ("SPY", "QQQ", "IWM", "TLT")


class Signal(BaseModel):
    """One strategy-selection decision, with the interpretable rationale behind it."""

    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    as_of: str | None = None
    strategy: str
    target_weights: dict[str, float]
    confidence: float | None = None
    rule_trace: list[str]

    def model_post_init(self, __context) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        total = sum(self.target_weights.values())
        if total > 1.0 + 1e-6:
            raise ValueError(f"target_weights must sum to <= 1.0, got {total}")


class SignalProvider(ABC):
    """Anything that can produce the current trading signal."""

    @abstractmethod
    def get_current_signal(self) -> Signal:
        raise NotImplementedError
