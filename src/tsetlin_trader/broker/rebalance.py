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


@dataclass
class RebalancePlan:
    trades: list[Trade]
    ignored_symbols: list[str]


def plan_rebalance(
    current: dict[str, float],
    target: dict[str, float],
    equity: float,
    universe: tuple[str, ...],
    band: float = 0.005,
) -> RebalancePlan:
    """Sells come first so their proceeds are available for the buys.

    Only symbols in `universe` are ever traded. Anything else held in the
    account is reported in `ignored_symbols` and left alone, so the bot can
    never liquidate a position it did not create.

    Differences smaller than `band * equity` are ignored to avoid churning
    on tiny drift. A symbol with a zero target is liquidated outright.
    """
    outside = sorted(s for s in target if s not in universe)
    if outside:
        raise ValueError(f"target contains symbols outside the universe: {outside}")

    threshold = band * equity
    sells: list[Trade] = []
    buys: list[Trade] = []
    ignored = sorted(s for s in current if s not in universe)

    for symbol in sorted((set(current) | set(target)) & set(universe)):
        held = current.get(symbol, 0.0)
        wanted = target.get(symbol, 0.0)
        delta = wanted - held

        if wanted <= 0 and held > 0:
            sells.append(Trade(symbol, "sell", round(held, 2), close_all=True))
        elif delta > threshold:
            buys.append(Trade(symbol, "buy", round(delta, 2)))
        elif -delta > threshold:
            sells.append(Trade(symbol, "sell", round(-delta, 2)))

    return RebalancePlan(sells + buys, ignored)


def execute(
    broker: BrokerClient,
    trades: list[Trade],
    cycle_id: str,
    fill_timeout_s: float = 120.0,
) -> list[OrderResult]:
    """Submit sells, confirm their final filled status, then submit buys.

    Each order carries a client id derived from the cycle and the trade, so a
    retry of the same cycle cannot place the same order twice. A timed-out,
    rejected or canceled sell prevents buys, even when no order remains open.
    """
    results: list[OrderResult] = []
    sells = [t for t in trades if t.side == "sell"]
    buys = [t for t in trades if t.side == "buy"]

    for trade in sells:
        if trade.close_all:
            results.append(broker.close_position(trade.symbol))
        else:
            results.append(broker.submit_order(trade.symbol, trade.notional, "sell", _client_id(cycle_id, trade)))

    sells_cleared = not sells or broker.wait_for_open_orders(fill_timeout_s)
    if sells_cleared:
        for result in results:
            if result.order_id and str(result.status).lower() not in ("filled", "orderstatus.filled"):
                result.status = broker.get_order_status(result.order_id)

    if not sells_cleared or any(str(result.status).lower() not in ("filled", "orderstatus.filled")
                                for result in results):
        for trade in buys:
            results.append(OrderResult(trade.symbol, 0.0, "buy", "skipped_sells_not_filled"))
        return results

    for trade in buys:
        results.append(broker.submit_order(trade.symbol, trade.notional, "buy", _client_id(cycle_id, trade)))
    return results


def _client_id(cycle_id: str, trade: Trade) -> str:
    return f"tt-{cycle_id}-{trade.symbol}-{trade.side}"[:48]

