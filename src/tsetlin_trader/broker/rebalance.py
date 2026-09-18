"""Turns "where we are" and "where we want to be" into a list of trades."""

from __future__ import annotations

from dataclasses import dataclass

from .base import BrokerClient, OrderResult


@dataclass(frozen=True)
class Trade:
    symbol: str
    side: str
    notional: float
    close_all: bool = False


def plan_rebalance(
    current: dict[str, float],
    target: dict[str, float],
    equity: float,
    band: float = 0.005,
) -> list[Trade]:
    """Sells come first so their proceeds are available for the buys.

    Differences smaller than `band * equity` are ignored to avoid churning
    on tiny drift. A symbol with a zero target is liquidated outright.
    """
    threshold = band * equity
    sells: list[Trade] = []
    buys: list[Trade] = []

    for symbol in sorted(set(current) | set(target)):
        held = current.get(symbol, 0.0)
        wanted = target.get(symbol, 0.0)
        delta = wanted - held

        if wanted <= 0 and held > 0:
            sells.append(Trade(symbol, "sell", round(held, 2), close_all=True))
        elif delta > threshold:
            buys.append(Trade(symbol, "buy", round(delta, 2)))
        elif -delta > threshold:
            sells.append(Trade(symbol, "sell", round(-delta, 2)))

    return sells + buys


def execute(broker: BrokerClient, trades: list[Trade]) -> list[OrderResult]:
    results = []
    for trade in trades:
        if trade.close_all:
            results.append(broker.close_position(trade.symbol))
        else:
            results.append(broker.submit_order(trade.symbol, trade.notional, trade.side))
    return results
