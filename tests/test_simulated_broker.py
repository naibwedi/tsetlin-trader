from tsetlin_trader.broker.simulated_client import SimulatedBroker


def test_starting_account():
    broker = SimulatedBroker(starting_cash=50_000)
    account = broker.get_account()

    assert account.equity == 50_000
    assert account.cash == 50_000


def test_submit_order_moves_cash_to_position():
    broker = SimulatedBroker(starting_cash=100_000)
    result = broker.submit_order("SPY", target_weight=0.2, equity=100_000)

    assert result.status == "filled"
    assert result.notional == 20_000
    assert broker.get_position_value("SPY") == 20_000
    assert broker.get_account().cash == 80_000
    assert broker.get_account().equity == 100_000


def test_submit_order_rejects_insufficient_cash():
    broker = SimulatedBroker(starting_cash=1_000)
    result = broker.submit_order("SPY", target_weight=0.5, equity=100_000)

    assert result.status == "rejected_insufficient_cash"
    assert broker.get_position_value("SPY") == 0.0


def test_submit_order_skips_zero_weight():
    broker = SimulatedBroker()
    result = broker.submit_order("SPY", target_weight=0.0, equity=100_000)

    assert result.status == "skipped_zero_notional"


def test_submit_order_rejects_negative_weight():
    import pytest

    broker = SimulatedBroker()
    with pytest.raises(ValueError):
        broker.submit_order("SPY", target_weight=-0.1, equity=100_000)
