"""Deterministic stand-in signal for offline tests and demos.

Uses the same strategy names as the real logic-alpha-tm model (trend,
momentum, defensive, cash) so it is a drop-in replacement, but it does no
modelling: it just cycles through them by ISO week.
"""

from __future__ import annotations

from datetime import date

from .base import Signal, SignalProvider

_STRATEGIES: dict[str, dict[str, float]] = {
    "trend": {"SPY": 1.0},
    "momentum": {"QQQ": 1.0},
    "defensive": {"TLT": 1.0},
    "cash": {},
}


class MockSignalProvider(SignalProvider):
    def get_current_signal(self) -> Signal:
        week = date.today().isocalendar().week
        names = list(_STRATEGIES)
        strategy = names[week % len(names)]

        return Signal(
            as_of=date.today().isoformat(),
            strategy=strategy,
            target_weights=_STRATEGIES[strategy],
            confidence=0.5,
            rule_trace=[f"MOCK: ISO week {week} % {len(names)} -> '{strategy}' (no model was run)"],
        )
