"""The broker seam. `run_cycle.py` depends only on this interface — the
signal, risk, and logging layers don't know or care whether orders end up
at Alpaca, another paper-trading provider, or a purely local simulator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date


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
    order_id: str | None = None
    client_order_id: str | None = None


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
    def is_trading_day(self, day: date) -> bool:
        """True if the exchange is open on `day`."""
        raise NotImplementedError

    @abstractmethod
    def wait_for_open_orders(self, timeout_s: float) -> bool:
        """Block until no orders are open. Returns False if the timeout passed first."""
        raise NotImplementedError

    @abstractmethod
    def submit_order(
        self, symbol: str, notional: float, side: str, client_order_id: str | None = None
    ) -> OrderResult:
        """Place a market order for `notional` dollars. `side` is 'buy' or 'sell'.

        `client_order_id` makes the order idempotent: resubmitting the same id
        must not create a second order.
        """
        raise NotImplementedError

    @abstractmethod
    def close_position(self, symbol: str) -> OrderResult:
        """Liquidate the entire position in `symbol`."""
        raise NotImplementedError
