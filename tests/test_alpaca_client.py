from types import SimpleNamespace

import pytest

from tsetlin_trader.broker.alpaca_client import AlpacaClient


class FakeTradingClient:
    def __init__(self, open_orders=()):
        self.submitted = []
        self.closed = []
        self._open_orders = list(open_orders)

    def get_account(self):
        return SimpleNamespace(equity="100000.00", cash="50000.00", buying_power="150000.00")

    def get_all_positions(self):
        return [
            SimpleNamespace(symbol="SPY", market_value="25000.50"),
            SimpleNamespace(symbol="IWM", market_value="10000.00"),
        ]

    def get_orders(self, request):
        return [SimpleNamespace(symbol=s) for s in self._open_orders]

    def submit_order(self, request):
        self.submitted.append(request)
        return SimpleNamespace(status="accepted")

    def close_position(self, symbol):
        self.closed.append(symbol)
        return SimpleNamespace(status="pending_new")


def make_client(**kwargs) -> tuple[AlpacaClient, FakeTradingClient]:
    fake = FakeTradingClient(**kwargs)
    return AlpacaClient(api_key="x", secret_key="y", trading_client=fake), fake


def test_get_account():
    client, _ = make_client()
    account = client.get_account()

    assert account.equity == 100_000.0
    assert account.cash == 50_000.0


def test_get_positions_returns_market_values():
    client, _ = make_client()
    assert client.get_positions() == {"SPY": 25000.50, "IWM": 10000.0}


def test_open_order_symbols():
    client, _ = make_client(open_orders=["SPY", "QQQ"])
    assert client.open_order_symbols() == {"SPY", "QQQ"}


def test_submit_buy_order():
    client, fake = make_client()
    result = client.submit_order("QQQ", 10_000.0, "buy")

    assert result.notional == 10_000.0
    assert result.side == "buy"
    assert len(fake.submitted) == 1
    assert fake.submitted[0].notional == 10_000.0


def test_submit_sell_order_uses_sell_side():
    from alpaca.trading.enums import OrderSide

    client, fake = make_client()
    client.submit_order("SPY", 500.0, "sell")

    assert fake.submitted[0].side == OrderSide.SELL


def test_submit_order_below_minimum_is_skipped():
    client, fake = make_client()
    result = client.submit_order("QQQ", 0.5, "buy")

    assert result.status == "skipped_below_minimum_notional"
    assert fake.submitted == []


def test_submit_order_rejects_bad_side():
    client, _ = make_client()
    with pytest.raises(ValueError):
        client.submit_order("QQQ", 100.0, "short")


def test_close_position():
    client, fake = make_client()
    result = client.close_position("IWM")

    assert fake.closed == ["IWM"]
    assert result.side == "sell"
