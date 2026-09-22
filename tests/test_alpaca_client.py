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

    def get_order_by_id(self, order_id):
        return SimpleNamespace(status="filled" if order_id == "ord-2" else "canceled")

    def get_order_by_client_id(self, client_id):
        request = next(r for r in self.submitted if r.client_order_id == client_id)
        return SimpleNamespace(symbol=request.symbol, notional=request.notional,
                               side=request.side, status="filled", id="ord-1")


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

    assert result.status == "filled"
    assert result.order_id == "ord-1"
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


def test_full_session_check_rejects_early_close():
    from datetime import date, datetime

    client, fake = make_client()
    fake.get_calendar = lambda request: [SimpleNamespace(
        date=request.start, close=datetime(2026, 11, 27, 13, 0))]
    assert client.is_full_trading_day(date(2026, 11, 27)) is False
    fake.get_calendar = lambda request: [SimpleNamespace(
        date=request.start, close=datetime(2026, 11, 30, 16, 0))]
    assert client.is_full_trading_day(date(2026, 11, 30)) is True


def test_get_order_status_uses_the_broker_record():
    client, _ = make_client()
    assert client.get_order_status("ord-2") == "filled"
    assert client.get_order_status("ord-1") == "canceled"


def test_is_trading_day_uses_the_exchange_calendar():
    from datetime import date

    client, _ = make_client()
    assert client.is_trading_day(date(2026, 9, 18)) is True
    assert client.is_trading_day(date(2026, 9, 19)) is False


def test_order_lookup_only_treats_404_as_absent():
    client, fake = make_client()
    class Missing(Exception):
        status_code = 404
    def absent(cid):
        raise Missing()
    fake.get_order_by_client_id = absent
    assert client.find_order("not-found") is None
    def outage(cid):
        raise TimeoutError()
    fake.get_order_by_client_id = outage
    with pytest.raises(TimeoutError):
        client.find_order("unknown")


def test_quantity_exit_reuses_client_id_instead_of_duplicate_sell():
    client, fake = make_client()
    class Missing(Exception):
        status_code = 404
    original = fake.get_order_by_client_id
    def lookup(cid):
        if not fake.submitted:
            raise Missing()
        return original(cid)
    fake.get_order_by_client_id = lookup
    fake.get_open_position = lambda symbol: SimpleNamespace(qty="2.5")
    first = client.close_position_idempotent("SPY", "exit-test")
    second = client.close_position_idempotent("SPY", "exit-test")
    assert first.order_id == second.order_id == "ord-1"
    assert len(fake.submitted) == 1
    assert fake.submitted[0].qty == 2.5


def test_timeout_after_acceptance_recovers_broker_order():
    client, fake = make_client()
    original = fake.submit_order
    def timeout(request):
        original(request)
        raise TimeoutError("response lost")
    fake.submit_order = timeout
    result = client.submit_order("SPY", 1000, "buy", "entry-test")
    assert result.order_id == "ord-1" and result.status == "filled"
    assert len(fake.submitted) == 1

