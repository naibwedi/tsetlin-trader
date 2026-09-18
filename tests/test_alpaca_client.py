from types import SimpleNamespace

import pytest

from tsetlin_trader.broker.alpaca_client import AlpacaClient


class FakeTradingClient:
    def __init__(self, open_orders=(), open_polls_before_clear=0):
        self.submitted = []
        self.closed = []
        self._open_orders = list(open_orders)
        self._polls_left = open_polls_before_clear
        self.polls = 0

    def get_account(self):
        return SimpleNamespace(equity="100000.00", cash="50000.00", buying_power="150000.00")

    def get_all_positions(self):
        return [
            SimpleNamespace(symbol="SPY", market_value="25000.50"),
            SimpleNamespace(symbol="IWM", market_value="10000.00"),
        ]

    def get_orders(self, request):
        self.polls += 1
        if self._polls_left > 0:
            self._polls_left -= 1
            return [SimpleNamespace(symbol=s) for s in self._open_orders]
        return []

    def submit_order(self, request):
        if any(getattr(r, "client_order_id", None) == request.client_order_id and request.client_order_id
               for r in self.submitted):
            raise Exception("client_order_id must be unique")
        self.submitted.append(request)
        return SimpleNamespace(status="accepted", id="ord-1")

    def get_calendar(self, request):
        from datetime import date
        return [] if request.start == date(2026, 9, 19) else [SimpleNamespace(date=request.start)]

    def close_position(self, symbol):
        self.closed.append(symbol)
        return SimpleNamespace(status="pending_new", id="ord-2")


def make_client(**kwargs):
    fake = FakeTradingClient(**kwargs)
    return AlpacaClient(api_key="x", secret_key="y", trading_client=fake, poll_s=0), fake


def test_get_account():
    client, _ = make_client()
    account = client.get_account()

    assert account.equity == 100_000.0
    assert account.cash == 50_000.0


def test_get_positions_returns_market_values():
    client, _ = make_client()
    assert client.get_positions() == {"SPY": 25000.50, "IWM": 10000.0}


def test_open_order_symbols():
    client, _ = make_client(open_orders=["SPY", "QQQ"], open_polls_before_clear=1)
    assert client.open_order_symbols() == {"SPY", "QQQ"}


def test_wait_for_open_orders_polls_until_clear():
    client, fake = make_client(open_orders=["SPY"], open_polls_before_clear=3)
    assert client.wait_for_open_orders(timeout_s=5) is True
    assert fake.polls >= 4


def test_wait_for_open_orders_times_out():
    client, _ = make_client(open_orders=["SPY"], open_polls_before_clear=10_000)
    assert client.wait_for_open_orders(timeout_s=0) is False


def test_submit_buy_order_carries_client_id():
    client, fake = make_client()
    result = client.submit_order("QQQ", 10_000.0, "buy", client_order_id="tt-1-QQQ-buy")

    assert result.notional == 10_000.0
    assert result.order_id == "ord-1"
    assert fake.submitted[0].client_order_id == "tt-1-QQQ-buy"


def test_duplicate_client_id_is_reported_not_raised():
    client, fake = make_client()
    client.submit_order("QQQ", 10_000.0, "buy", client_order_id="dup")
    result = client.submit_order("QQQ", 10_000.0, "buy", client_order_id="dup")

    assert result.status == "duplicate_client_order_id"
    assert len(fake.submitted) == 1


def test_submit_sell_order_uses_sell_side():
    from alpaca.trading.enums import OrderSide

    client, fake = make_client()
    client.submit_order("SPY", 500.0, "sell")

    assert fake.submitted[0].side == OrderSide.SELL


def test_submit_order_below_minimum_is_skipped():
    client, fake = make_client()
    assert client.submit_order("QQQ", 0.5, "buy").status == "skipped_below_minimum_notional"
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
    assert result.order_id == "ord-2"


def test_is_trading_day_uses_the_exchange_calendar():
    from datetime import date

    client, _ = make_client()
    assert client.is_trading_day(date(2026, 9, 18)) is True
    assert client.is_trading_day(date(2026, 9, 19)) is False
