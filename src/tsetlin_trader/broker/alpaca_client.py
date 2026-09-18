"""Thin wrapper around alpaca-py, hardcoded to the paper trading endpoint.

The underlying Alpaca `TradingClient` can be injected (see tests) so this
module is fully testable offline, with no real network calls.
"""

from __future__ import annotations

from .base import AccountSnapshot, BrokerClient, OrderResult


class AlpacaClient(BrokerClient):
    """Paper-trading-only client. Always constructed against Alpaca's paper
    endpoint — this repo does not place live trades."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        trading_client=None,
    ) -> None:
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

    def get_position_value(self, symbol: str) -> float:
        try:
            position = self._client.get_open_position(symbol)
            return float(position.market_value)
        except Exception:
            return 0.0

    def submit_order(self, symbol: str, target_weight: float, equity: float) -> OrderResult:
        if target_weight < 0:
            raise ValueError("target_weight must be >= 0 (long-only)")

        notional = round(equity * target_weight, 2)
        side = "buy"

        if notional <= 0:
            return OrderResult(symbol=symbol, notional=0.0, side=side, status="skipped_zero_notional")

        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest

        order_request = MarketOrderRequest(
            symbol=symbol,
            notional=notional,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        order = self._client.submit_order(order_request)
        return OrderResult(
            symbol=symbol,
            notional=notional,
            side=side,
            status=getattr(order, "status", "submitted"),
        )
