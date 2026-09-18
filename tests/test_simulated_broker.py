import pytest

from tsetlin_trader.broker.simulated_client import SimulatedBroker


def test_starting_account():
    account = SimulatedBroker(starting_cash=50_000).get_account()

    assert account.equity == 50_000
    assert account.cash == 50_000


def test_buy_moves_cash_into_position():
    broker = SimulatedBroker(starting_cash=100_000)
    result = broker.submit_order("SPY", 20_000, "buy")

    assert result.status == "filled"
    assert broker.get_positions() == {"SPY": 20_000}
    assert broker.get_account().cash == 80_000
    assert broker.get_account().equity == 100_000


def test_buy_rejected_when_insufficient_cash():
    broker = SimulatedBroker(starting_cash=1_000)
    result = broker.submit_order("SPY", 50_000, "buy")

    assert result.status == "rejected_insufficient_cash"
    assert broker.get_positions() == {}


def test_partial_sell_returns_cash():
    broker = SimulatedBroker(starting_cash=100_000)
    broker.submit_order("SPY", 20_000, "buy")
    broker.submit_order("SPY", 5_000, "sell")

    assert broker.get_positions() == {"SPY": 15_000}
    assert broker.get_account().cash == 85_000


def test_sell_more_than_held_is_rejected():
    broker = SimulatedBroker()
    broker.submit_order("SPY", 1_000, "buy")

    assert broker.submit_order("SPY", 2_000, "sell").status == "rejected_insufficient_position"


def test_close_position_liquidates_everything():
    broker = SimulatedBroker(starting_cash=100_000)
    broker.submit_order("IWM", 10_000, "buy")
    broker.close_position("IWM")

    assert broker.get_positions() == {}
    assert broker.get_account().cash == 100_000


def test_close_position_without_holding_is_a_noop():
    assert SimulatedBroker().close_position("SPY").status == "skipped_no_position"


def test_zero_notional_is_skipped():
    assert SimulatedBroker().submit_order("SPY", 0, "buy").status == "skipped_zero_notional"


def test_rejects_bad_side():
    with pytest.raises(ValueError):
        SimulatedBroker().submit_order("SPY", 100, "short")
