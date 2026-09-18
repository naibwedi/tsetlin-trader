"""A purely local paper broker — no API keys, no network, no external account.

Lets anyone clone this repo and run a full trading cycle immediately
(`BROKER_PROVIDER=simulated`, the default). It tracks cash and notional
position value in memory; it does not simulate market price movement, so
it's for exercising the pipeline end-to-end, not for realistic P&L. Swap
in `AlpacaClient` (or any other `BrokerClient`) for that.
"""

from __future__ import annotations

from .base import AccountSnapshot, BrokerClient, OrderResult


class SimulatedBroker(BrokerClient):
    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._cash = starting_cash
        self._starting_equity = starting_cash
        self._positions: dict[str, float] = {}

    def get_account(self) -> AccountSnapshot:
        equity = self._cash + sum(self._positions.values())
        return AccountSnapshot(equity=equity, cash=self._cash, buying_power=self._cash)

    def get_position_value(self, symbol: str) -> float:
        return self._positions.get(symbol, 0.0)

    def submit_order(self, symbol: str, target_weight: float, equity: float) -> OrderResult:
        if target_weight < 0:
            raise ValueError("target_weight must be >= 0 (long-only)")

        notional = round(equity * target_weight, 2)
        if notional <= 0:
            return OrderResult(symbol=symbol, notional=0.0, side="buy", status="skipped_zero_notional")
        if notional > self._cash:
            return OrderResult(symbol=symbol, notional=0.0, side="buy", status="rejected_insufficient_cash")

        self._cash -= notional
        self._positions[symbol] = self._positions.get(symbol, 0.0) + notional

        return OrderResult(symbol=symbol, notional=notional, side="buy", status="filled")
