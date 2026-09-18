"""Thin wrapper around alpaca-py, hardcoded to the paper trading endpoint.

The underlying Alpaca `TradingClient` can be injected (see tests) so this
module is fully testable offline, with no real network calls.
"""

from __future__ import annotations

from .base import AccountSnapshot, BrokerClient, OrderResult


class AlpacaClient(BrokerClient):
    """Paper-trading-only client. Always constructed against Alpaca's paper
    endpoint — this repo does not place live trades."""

    def __init__(self, api_key: str, secret_key: str, trading_client=None) -> None:
        if trading_client is not None:
            self._client = trading_client
        else:
            from alpaca.trading.client import TradingClient

            self._client = TradingClient(api_key, secret_key, paper=True)

    def get_account(self) -> AccountSnapshot:
        acct = self._client.get_account()
        return AccountSnapshot(
            equity=float(acct.equity),
            cash=float(acct.cash),
            buying_power=float(acct.buying_power),
        )

    def get_positions(self) -> dict[str, float]:
        return {p.symbol: float(p.market_value) for p in self._client.get_all_positions()}

    def open_order_symbols(self) -> set[str]:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        orders = self._client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return {o.symbol for o in orders}

    def submit_order(self, symbol: str, notional: float, side: str) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        notional = round(notional, 2)
        if notional < 1.0:
            return OrderResult(symbol, 0.0, side, "skipped_below_minimum_notional")

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(request)
        return OrderResult(symbol, notional, side, str(getattr(order, "status", "submitted")))

    def close_position(self, symbol: str) -> OrderResult:
        order = self._client.close_position(symbol)
        return OrderResult(symbol, 0.0, "sell", str(getattr(order, "status", "submitted")))
