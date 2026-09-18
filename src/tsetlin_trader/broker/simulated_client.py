"""A purely local paper broker — no API keys, no network, no external account.

Lets anyone clone this repo and run a full trading cycle immediately
(`BROKER_PROVIDER=simulated`). It tracks cash and notional position value in
memory; it does not simulate market price movement, so it exercises the
pipeline end-to-end but says nothing about P&L. Use `AlpacaClient` for that.
"""

from __future__ import annotations

from .base import AccountSnapshot, BrokerClient, OrderResult


class SimulatedBroker(BrokerClient):
    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self._cash = starting_cash
        self._positions: dict[str, float] = {}

    def get_account(self) -> AccountSnapshot:
        equity = self._cash + sum(self._positions.values())
        return AccountSnapshot(equity=equity, cash=self._cash, buying_power=self._cash)

    def get_positions(self) -> dict[str, float]:
        return dict(self._positions)

    def open_order_symbols(self) -> set[str]:
        return set()

    def submit_order(self, symbol: str, notional: float, side: str) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if notional <= 0:
            return OrderResult(symbol, 0.0, side, "skipped_zero_notional")

        if side == "buy":
            if notional > self._cash:
                return OrderResult(symbol, 0.0, side, "rejected_insufficient_cash")
            self._cash -= notional
            self._positions[symbol] = self._positions.get(symbol, 0.0) + notional
        else:
            held = self._positions.get(symbol, 0.0)
            if notional > held + 1e-9:
                return OrderResult(symbol, 0.0, side, "rejected_insufficient_position")
            self._cash += notional
            self._positions[symbol] = held - notional
            if self._positions[symbol] <= 1e-9:
                del self._positions[symbol]

        return OrderResult(symbol, notional, side, "filled")

    def close_position(self, symbol: str) -> OrderResult:
        held = self._positions.get(symbol, 0.0)
        if held <= 0:
            return OrderResult(symbol, 0.0, "sell", "skipped_no_position")
        return self.submit_order(symbol, held, "sell")
