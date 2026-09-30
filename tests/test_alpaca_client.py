from datetime import date

import pytest

from tsetlin_trader.broker.alpaca_client import AlpacaClient, AlpacaHTTPError, PAPER_BASE


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.orders = {}
        self.open_polls = 0

    def request(self, method, path, params=None, body=None):
        self.calls.append((method, path, params, body))
        if path == "/v2/account":
            return {"id": "paper-1", "equity": "100000", "cash": "50000", "buying_power": "150000"}
        if path == "/v2/positions":
            return [{"symbol": "SPY", "market_value": "25000.50"}]
        if path == "/v2/positions/SPY":
            return {"symbol": "SPY", "qty": "2.5"}
        if path == "/v2/calendar":
            return [] if params["start"] == "2026-09-19" else [{"date": params["start"], "close": "16:00"}]
        if path == "/v2/clock":
            return {"is_open": True}
        if path == "/v2/orders" and method == "GET":
            if self.open_polls:
                self.open_polls -= 1
                return [{"symbol": "SPY"}]
            return []
        if path == "/v2/orders" and method == "POST":
            cid = body.get("client_order_id")
            order = {**body, "id": "ord-1", "status": "accepted"}
            if cid in self.orders:
                raise AlpacaHTTPError(422, "duplicate")
            if cid:
                self.orders[cid] = order
            return order
        if path == "/v2/orders:by_client_order_id":
            if params["client_order_id"] not in self.orders:
                raise AlpacaHTTPError(404, "not found")
            return {**self.orders[params["client_order_id"]], "status": "filled"}
        if path == "/v2/orders/ord-1":
            return {"status": "filled"}
        if path == "/v2/positions/IWM" and method == "DELETE":
            return {"symbol": "IWM", "side": "sell", "id": "ord-2", "status": "pending_new"}
        return None


def make_client():
    transport = FakeTransport()
    return AlpacaClient("key", "secret", transport=transport, poll_s=0), transport


def test_account_positions_and_identity():
    client, _ = make_client()
    assert client.get_account().equity == 100000
    assert client.get_positions() == {"SPY": 25000.5}
    assert client.account_identity() == "paper-1"


def test_market_calendar_and_clock():
    client, _ = make_client()
    assert client.is_trading_day(date(2026, 9, 18))
    assert not client.is_trading_day(date(2026, 9, 19))
    assert client.is_full_trading_day(date(2026, 9, 18))
    assert client.is_market_open()


def test_waits_for_open_orders_and_times_out():
    client, rest = make_client()
    rest.open_polls = 2
    assert client.wait_for_open_orders(2)
    rest.open_polls = 2
    assert not client.wait_for_open_orders(0)


def test_submit_uses_paper_json_and_recovers_duplicate():
    client, rest = make_client()
    first = client.submit_order("QQQ", 1000, "buy", "cycle-qqq-buy")
    second = client.submit_order("QQQ", 1000, "buy", "cycle-qqq-buy")
    assert first.order_id == second.order_id == "ord-1"
    post = next(c for c in rest.calls if c[0] == "POST")
    assert post[3] == {"symbol": "QQQ", "notional": "1000", "side": "buy",
                       "type": "market", "time_in_force": "day",
                       "client_order_id": "cycle-qqq-buy"}


def test_timeout_after_acceptance_recovers_order():
    client, rest = make_client()
    original = rest.request
    def request(method, path, params=None, body=None):
        result = original(method, path, params, body)
        if method == "POST":
            raise TimeoutError("response lost")
        return result
    rest.request = request
    assert client.submit_order("SPY", 500, "buy", "lost-response").status == "filled"


def test_idempotent_close_uses_exact_long_quantity():
    client, rest = make_client()
    first = client.close_position_idempotent("SPY", "cycle-spy-exit")
    second = client.close_position_idempotent("SPY", "cycle-spy-exit")
    assert first.order_id == second.order_id == "ord-1"
    posts = [c for c in rest.calls if c[0] == "POST"]
    assert len(posts) == 1 and posts[0][3]["qty"] == "2.5"


def test_lookup_only_404_is_absent():
    client, rest = make_client()
    assert client.find_order("missing") is None
    rest.request = lambda *a, **k: (_ for _ in ()).throw(AlpacaHTTPError(401, "bad keys"))
    with pytest.raises(AlpacaHTTPError):
        client.find_order("unknown")


def test_close_cancel_status_and_validation():
    client, rest = make_client()
    assert client.close_position("IWM").order_id == "ord-2"
    assert client.get_order_status("ord-1") == "filled"
    client.cancel_order("ord-1")
    assert ("DELETE", "/v2/orders/ord-1", None, None) in rest.calls
    assert client.submit_order("QQQ", .5, "buy").status.startswith("skipped_")
    with pytest.raises(ValueError):
        client.submit_order("QQQ", 5, "short")


def test_production_endpoint_is_paper_only():
    assert PAPER_BASE == "https://paper-api.alpaca.markets"
