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
    """Anything that can report account state and place a long-only order."""

    @abstractmethod
    def get_account(self) -> AccountSnapshot:
        raise NotImplementedError

    @abstractmethod
    def get_position_value(self, symbol: str) -> float:
        raise NotImplementedError

    @abstractmethod
    def submit_order(self, symbol: str, target_weight: float, equity: float) -> OrderResult:
        """Place an order sized as `target_weight * equity` notional. Long-only."""
        raise NotImplementedError
