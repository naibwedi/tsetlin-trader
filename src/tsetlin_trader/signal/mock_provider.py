"""Deterministic stand-in for the real logic-alpha-tm Tsetlin Machine signal.

Lets the rest of the pipeline (risk, broker, logging) run and be tested
end-to-end today. Replace with a real SignalProvider implementation once
the research model is wired in — nothing else needs to change.
"""

from __future__ import annotations

from datetime import date

from .base import UNIVERSE, Signal, SignalProvider

_STRATEGIES: dict[str, dict[str, float]] = {
    "risk_on": {"SPY": 0.5, "QQQ": 0.3},
    "risk_off": {"TLT": 0.6},
    "small_cap_tilt": {"IWM": 0.4, "SPY": 0.2},
    "balanced": {"SPY": 0.25, "QQQ": 0.15, "IWM": 0.1, "TLT": 0.2},
}


class MockSignalProvider(SignalProvider):
    """Cycles deterministically through a fixed set of strategies by ISO week."""

    def get_current_signal(self) -> Signal:
        week = date.today().isocalendar().week
        names = list(_STRATEGIES.keys())
        strategy = names[week % len(names)]
        weights = _STRATEGIES[strategy]

        return Signal(
            strategy=strategy,
            target_weights=weights,
            confidence=0.7,
            rule_trace=[
                f"MOCK: week {week} % {len(names)} -> '{strategy}'",
                f"MOCK: no real Tsetlin Machine clauses fired (universe={UNIVERSE})",
            ],
        )
