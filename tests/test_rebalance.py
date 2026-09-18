from tsetlin_trader.broker.rebalance import Trade, execute, plan_rebalance
from tsetlin_trader.broker.simulated_client import SimulatedBroker


def test_buys_to_reach_target_from_flat():
    trades = plan_rebalance({}, {"SPY": 25_000}, equity=100_000)
    assert trades == [Trade("SPY", "buy", 25_000)]


def test_sells_come_before_buys():
    trades = plan_rebalance({"IWM": 10_000}, {"SPY": 25_000}, equity=100_000)

    assert [t.side for t in trades] == ["sell", "buy"]
    assert trades[0] == Trade("IWM", "sell", 10_000, close_all=True)


def test_zero_target_closes_the_position():
    trades = plan_rebalance({"SPY": 5_000}, {}, equity=100_000)
    assert trades == [Trade("SPY", "sell", 5_000, close_all=True)]


def test_partial_reduction_is_a_notional_sell():
    trades = plan_rebalance({"SPY": 30_000}, {"SPY": 25_000}, equity=100_000)
    assert trades == [Trade("SPY", "sell", 5_000)]


def test_drift_inside_band_is_ignored():
    # 0.5% of 100k = 500; a 300 gap should not trade
    assert plan_rebalance({"SPY": 25_000}, {"SPY": 25_300}, equity=100_000) == []


def test_already_at_target_does_nothing():
    assert plan_rebalance({"SPY": 25_000}, {"SPY": 25_000}, equity=100_000) == []


def test_execute_against_simulated_broker_reaches_target():
    broker = SimulatedBroker(starting_cash=100_000)
    broker.submit_order("IWM", 10_000, "buy")

    trades = plan_rebalance(broker.get_positions(), {"SPY": 25_000}, broker.get_account().equity)
    execute(broker, trades)

    assert broker.get_positions() == {"SPY": 25_000}
    assert plan_rebalance(broker.get_positions(), {"SPY": 25_000}, broker.get_account().equity) == []
