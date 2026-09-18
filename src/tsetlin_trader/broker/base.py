"""The broker seam. `run_cycle.py` depends only on this interface — the
signal, risk, and logging layers don't know or care whether orders end up
at Alpaca, another paper-trading provider, or a purely local simulator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AccountSnapshot:
    equity: float
    cash: float
    buying_power: float


@dataclass
class OrderResult:
    symbol: str
    notional: float
    side: str
    status: str


class BrokerClient(ABC):
    """Anything that can report account state and place long-only orders."""

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_positions(self) -> dict[str, float]:
        """Current market value in dollars per held symbol."""
        raise NotImplementedError

    @abstractmethod
    def open_order_symbols(self) -> set[str]:
        """Symbols with orders submitted but not yet filled/cancelled."""
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, symbol: str, notional: float, side: str) -> OrderResult:
        """Place a market order for `notional` dollars. `side` is 'buy' or 'sell'."""
        raise NotImplementedError

    @abstractmethod
    def close_position(self, symbol: str) -> OrderResult:
        """Liquidate the entire position in `symbol`."""
        raise NotImplementedError
