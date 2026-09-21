from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tsetlin_trader.live_paper import Runner, decide
from tests.test_intraday_trial import bars

NY = ZoneInfo("America/New_York")


class FakeBroker:
    def __init__(self):
        self.positions = {}
        self.orders = []
        self._client = SimpleNamespace(get_clock=lambda: SimpleNamespace(is_open=True))

    def get_account(self):
        return SimpleNamespace(equity=100000, cash=100000)

    def account_identity(self):
        return "paper-test-account"

    def find_order(self, client_id):
        return None

    def get_positions(self):
        return self.positions.copy()

    def open_order_symbols(self):
        return set()

    def is_trading_day(self, day):
        return True

    def is_full_trading_day(self, day):
        return True

    def is_market_open(self):
        return True

    def cancel_order(self, order_id):
        self.orders.append(("SPY", 0, "cancel", order_id))

    def get_order_status(self, order_id):
        return "filled"

    def submit_order(self, symbol, notional, side, client_order_id):
        self.orders.append((symbol, notional, side, client_order_id))
        return SimpleNamespace(symbol=symbol, side=side,
                               status="pending_new", order_id="entry1")

    def close_position(self, symbol):
        self.orders.append((symbol, 0, "sell", None))
        return SimpleNamespace(symbol=symbol, side="sell",
                               status="pending_new", order_id="exit1")

    def close_position_idempotent(self, symbol, client_order_id):
        return self.close_position(symbol)


@pytest.fixture
def state_path():
    path = Path("results") / f".test-live-{uuid4().hex}.json"
    yield path
    path.unlink(missing_ok=True)
    path.with_suffix(".tmp").unlink(missing_ok=True)


def at(hour, minute, day=21):
    return datetime(2026, 9, day, hour, minute, tzinfo=NY)


def test_live_decision_uses_only_prior_exit_labels():
    frame = bars(35)
    last_day = pd.to_datetime(frame.t, utc=True).dt.tz_convert(NY).dt.date.max().isoformat()
    first = decide(frame, last_day)
    changed = frame.copy()
    changed.loc[changed.index[-1], "c"] = 1
    second = decide(changed, last_day)
    assert first == second


def test_paper_runner_starts_paused_and_never_duplicates_entry(monkeypatch, state_path):
    broker = FakeBroker()
    runner = Runner("paper", state_path, broker=broker, data_client=lambda *args: pd.DataFrame(), token="private")
    monkeypatch.setenv("INTRADAY_ALPACA_API_KEY", "test")
    monkeypatch.setenv("INTRADAY_ALPACA_SECRET_KEY", "test")
    monkeypatch.setattr("tsetlin_trader.live_paper.decide", lambda *args: {"day": "2026-09-21", "action": "buy_SPY"})
    runner.tick(at(10, 35))
    assert not broker.orders
    with pytest.raises(PermissionError):
        runner.control("resume", "wrong")
    runner.control("resume", "private", at(10, 31))
    runner.tick(at(10, 35))
    runner.tick(at(10, 35))
    assert len(broker.orders) == 1
    assert broker.orders[0][1] == 5000
    assert runner.snapshot()["entry_order"]["status"] == "filled"
    restarted = Runner("paper", state_path, broker=broker,
                       data_client=lambda *args: pd.DataFrame(), token="private")
    restarted.tick(at(10, 34))
    assert len(broker.orders) == 1


def test_flatten_only_bot_managed_position(monkeypatch, state_path):
    broker = FakeBroker()
    broker.positions = {"SPY": 1000}
    runner = Runner("paper", state_path, broker=broker, token="private")
    runner.control("flatten", "private", at(12, 0))
    assert broker.orders == []
    runner.state["managed_position"] = True
    runner.control("flatten", "private", at(12, 1))
    assert broker.orders[-1][2] == "sell"


def test_observe_mode_skips_weekend_without_credentials(monkeypatch, state_path):
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    runner = Runner("observe", state_path)
    runner.tick(at(10, 32, day=19))
    assert runner.snapshot()["last_error"] is None

