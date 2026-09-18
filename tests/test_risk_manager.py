from tsetlin_trader.risk.manager import Decision, RiskManager


def test_trade_when_no_drawdown():
    rm = RiskManager(max_drawdown_pct=0.15, position_fraction=0.5)
    decision = rm.size_order({"SPY": 0.5, "QQQ": 0.3}, equity=100_000)

    assert decision.decision == Decision.TRADE
    assert decision.sized_weights == {"SPY": 0.25, "QQQ": 0.15}


def test_halts_on_max_drawdown():
    rm = RiskManager(max_drawdown_pct=0.10, position_fraction=1.0)
    rm.size_order({"SPY": 0.5}, equity=100_000)  # sets peak equity

    decision = rm.size_order({"SPY": 0.5}, equity=85_000)  # 15% drawdown

    assert decision.decision == Decision.HALT
    assert decision.sized_weights == {}


def test_tracks_new_peak_equity():
    rm = RiskManager(max_drawdown_pct=0.10, position_fraction=1.0)
    rm.size_order({"SPY": 0.5}, equity=100_000)
    rm.size_order({"SPY": 0.5}, equity=120_000)  # new peak

    decision = rm.size_order({"SPY": 0.5}, equity=110_000)  # ~8.3% off new peak

    assert decision.decision == Decision.TRADE


def test_rejects_invalid_max_drawdown():
    import pytest

    with pytest.raises(ValueError):
        RiskManager(max_drawdown_pct=1.5)
