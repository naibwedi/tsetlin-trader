"""Thin wrapper around alpaca-py, hardcoded to the paper trading endpoint.

The underlying Alpaca `TradingClient` can be injected (see tests) so this
module is fully testable offline, with no real network calls.
"""

from __future__ import annotations

import time
from datetime import date

from .base import AccountSnapshot, BrokerClient, OrderResult


class AlpacaClient(BrokerClient):
    """Paper-trading-only client. Always constructed against Alpaca's paper
    endpoint — this repo does not place live trades."""

    def __init__(self, api_key: str, secret_key: str, trading_client=None, poll_s: float = 2.0) -> None:
        self._poll_s = poll_s
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

    def is_trading_day(self, day: date) -> bool:
        from alpaca.trading.requests import GetCalendarRequest

        days = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        return any(getattr(d, "date", None) == day for d in days) or (len(days) > 0)

    def is_market_open(self) -> bool:
        return bool(self._client.get_clock().is_open)

    def is_full_trading_day(self, day: date) -> bool:
        from alpaca.trading.requests import GetCalendarRequest

        days = self._client.get_calendar(GetCalendarRequest(start=day, end=day))
        return any(getattr(session, "date", None) == day and
                   getattr(session, "close", None) is not None and
                   session.close.hour >= 16 for session in days)

    def cancel_order(self, order_id: str) -> None:
        self._client.cancel_order_by_id(order_id)

    def wait_for_open_orders(self, timeout_s: float) -> bool:
        deadline = time.monotonic() + timeout_s
        while self.open_order_symbols():
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._poll_s)
        return True

    def get_order_status(self, order_id: str) -> str:
        return str(self._client.get_order_by_id(order_id).status)

    def submit_order(
        self, symbol: str, notional: float, side: str, client_order_id: str | None = None
    ) -> OrderResult:
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        notional = round(notional, 2)
        if notional < 1.0:
            return OrderResult(symbol, 0.0, side, "skipped_below_minimum_notional", None, client_order_id)

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        request = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            client_order_id=client_order_id,
        )
        try:
            order = self._client.submit_order(request)
        except Exception as exc:  # Alpaca rejects a reused client_order_id with an API error
            if client_order_id and "client_order_id" in str(exc):
                return OrderResult(symbol, 0.0, side, "duplicate_client_order_id", None, client_order_id)
            raise
        return OrderResult(
            symbol, notional, side, str(getattr(order, "status", "submitted")),
            str(getattr(order, "id", "")) or None, client_order_id,
        )

    def close_position(self, symbol: str) -> OrderResult:
        order = self._client.close_position(symbol)
        return OrderResult(
            symbol, 0.0, "sell", str(getattr(order, "status", "submitted")),
            str(getattr(order, "id", "")) or None, None,
        )

