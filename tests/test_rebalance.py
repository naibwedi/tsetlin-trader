import pytest

from tsetlin_trader.broker.rebalance import Trade, execute, plan_rebalance
from tsetlin_trader.broker.base import OrderResult
from tsetlin_trader.broker.simulated_client import SimulatedBroker

U = ("SPY", "QQQ", "IWM", "TLT")


def test_buys_to_reach_target_from_flat():
    plan = plan_rebalance({}, {"SPY": 25_000}, 100_000, U)
    assert plan.trades == [Trade("SPY", "buy", 25_000)]


def test_sells_come_before_buys():
    plan = plan_rebalance({"IWM": 10_000}, {"SPY": 25_000}, 100_000, U)

    assert [t.side for t in plan.trades] == ["sell", "buy"]
    assert plan.trades[0] == Trade("IWM", "sell", 10_000, close_all=True)


def test_zero_target_closes_the_position():
    plan = plan_rebalance({"SPY": 5_000}, {}, 100_000, U)
    assert plan.trades == [Trade("SPY", "sell", 5_000, close_all=True)]


def test_partial_reduction_is_a_notional_sell():
    plan = plan_rebalance({"SPY": 30_000}, {"SPY": 25_000}, 100_000, U)
    assert plan.trades == [Trade("SPY", "sell", 5_000)]


def test_drift_inside_band_is_ignored():
    assert plan_rebalance({"SPY": 25_000}, {"SPY": 25_300}, 100_000, U).trades == []


def test_positions_outside_the_universe_are_never_touched():
    plan = plan_rebalance({"AAPL": 50_000, "SPY": 5_000}, {}, 100_000, U)

    assert plan.ignored_symbols == ["AAPL"]
    assert plan.trades == [Trade("SPY", "sell", 5_000, close_all=True)]


def test_target_outside_the_universe_is_rejected():
    with pytest.raises(ValueError, match="outside the universe"):
        plan_rebalance({}, {"AAPL": 1_000}, 100_000, U)


def test_execute_reaches_target_and_second_plan_is_empty():
    broker = SimulatedBroker(starting_cash=100_000)
    broker.submit_order("IWM", 10_000, "buy")

    plan = plan_rebalance(broker.get_positions(), {"SPY": 25_000}, broker.get_account().equity, U)
    execute(broker, plan.trades, cycle_id="c1")

    assert broker.get_positions() == {"SPY": 25_000}
    assert plan_rebalance(broker.get_positions(), {"SPY": 25_000}, broker.get_account().equity, U).trades == []


def test_retrying_the_same_cycle_does_not_double_order():
    broker = SimulatedBroker(starting_cash=100_000)
    trades = [Trade("SPY", "buy", 10_000)]

    first = execute(broker, trades, cycle_id="2026-09-17")
    second = execute(broker, trades, cycle_id="2026-09-17")

    assert first[0].status == "filled"
    assert second[0].status == "duplicate_client_order_id"
    assert broker.get_positions() == {"SPY": 10_000}


def test_buys_are_skipped_when_sells_do_not_fill():
    class StuckBroker(SimulatedBroker):
        def wait_for_open_orders(self, timeout_s):
            return False

    broker = StuckBroker(starting_cash=100_000)
    broker.submit_order("IWM", 10_000, "buy")
    trades = [Trade("IWM", "sell", 10_000, close_all=True), Trade("SPY", "buy", 10_000)]

    results = execute(broker, trades, cycle_id="c2")

    assert results[0].side == "sell"
    assert results[1].status == "skipped_sells_not_filled"
    assert "SPY" not in broker.get_positions()


def test_rejected_sell_cannot_be_followed_by_a_buy():
    class RejectingBroker(SimulatedBroker):
        def close_position(self, symbol):
            return OrderResult(symbol, 0.0, "sell", "rejected")

    broker = RejectingBroker()
    broker.submit_order("IWM", 10_000, "buy")
    results = execute(broker, [Trade("IWM", "sell", 10_000, True),
                               Trade("SPY", "buy", 10_000)], cycle_id="reject")

    assert [result.status for result in results] == ["rejected", "skipped_sells_not_filled"]
    assert broker.get_positions() == {"IWM": 10_000}


def test_closed_order_must_have_filled_before_buying():
    class CanceledBroker(SimulatedBroker):
        def close_position(self, symbol):
            return OrderResult(symbol, 10_000, "sell", "pending_new", "order-1")

        def get_order_status(self, order_id):
            return "canceled"

    broker = CanceledBroker()
    broker.submit_order("IWM", 10_000, "buy")
    results = execute(broker, [Trade("IWM", "sell", 10_000, True),
                               Trade("SPY", "buy", 10_000)], cycle_id="cancel")

    assert [result.status for result in results] == ["canceled", "skipped_sells_not_filled"]
    assert broker.get_positions() == {"IWM": 10_000}

