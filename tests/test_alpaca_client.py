from types import SimpleNamespace

from tsetlin_trader.broker.alpaca_client import AlpacaClient


class FakeTradingClient:
    def __init__(self):
        self.submitted_orders = []

    def get_account(self):
        return SimpleNamespace(equity="100000.00", cash="50000.00", buying_power="150000.00")

    def get_open_position(self, symbol):
        if symbol == "SPY":
            return SimpleNamespace(market_value="25000.00")
        raise Exception("position does not exist")

    def submit_order(self, order_request):
        self.submitted_orders.append(order_request)
        return SimpleNamespace(status="accepted")


def make_client() -> tuple[AlpacaClient, FakeTradingClient]:
    fake = FakeTradingClient()
    client = AlpacaClient(api_key="x", secret_key="y", trading_client=fake)
    return client, fake


def test_get_account():
    client, _ = make_client()
    account = client.get_account()

    assert account.equity == 100_000.0
    assert account.cash == 50_000.0


def test_get_position_value_existing():
    client, _ = make_client()
    assert client.get_position_value("SPY") == 25_000.0


def test_get_position_value_missing_returns_zero():
    client, _ = make_client()
    assert client.get_position_value("TLT") == 0.0


def test_submit_order_places_notional_order():
    client, fake = make_client()
    result = client.submit_order("QQQ", target_weight=0.1, equity=100_000)

    assert result.notional == 10_000.0
    assert result.status == "accepted"
    assert len(fake.submitted_orders) == 1


def test_submit_order_skips_zero_notional():
    client, fake = make_client()
    result = client.submit_order("QQQ", target_weight=0.0, equity=100_000)

    assert result.status == "skipped_zero_notional"
    assert len(fake.submitted_orders) == 0


def test_submit_order_rejects_negative_weight():
    import pytest

    client, _ = make_client()
    with pytest.raises(ValueError):
        client.submit_order("QQQ", target_weight=-0.1, equity=100_000)
