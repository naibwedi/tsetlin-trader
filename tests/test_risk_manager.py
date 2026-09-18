import pytest

from tsetlin_trader.risk.manager import Decision, RiskManager


def test_trade_when_no_drawdown():
    rm = RiskManager(max_drawdown_pct=0.15, position_fraction=0.5)
    decision = rm.size_order({"SPY": 0.5, "QQQ": 0.3}, equity=100_000)

    assert decision.decision == Decision.TRADE
    assert decision.sized_weights == {"SPY": 0.25, "QQQ": 0.15}


def test_halts_on_max_drawdown():
    rm = RiskManager(max_drawdown_pct=0.10, position_fraction=1.0)
    rm.size_order({"SPY": 0.5}, equity=100_000)

    decision = rm.size_order({"SPY": 0.5}, equity=85_000)

    assert decision.decision == Decision.HALT
    assert decision.sized_weights == {}


def test_halt_is_sticky_even_after_equity_recovers():
    rm = RiskManager(max_drawdown_pct=0.10, position_fraction=1.0)
    rm.size_order({"SPY": 0.5}, equity=100_000)
    rm.size_order({"SPY": 0.5}, equity=80_000)

    assert rm.size_order({"SPY": 0.5}, equity=100_000).decision == Decision.HALT


def test_tracks_new_peak_equity():
    rm = RiskManager(max_drawdown_pct=0.10, position_fraction=1.0)
    rm.size_order({"SPY": 0.5}, equity=100_000)
    rm.size_order({"SPY": 0.5}, equity=120_000)

    assert rm.size_order({"SPY": 0.5}, equity=110_000).decision == Decision.TRADE


def test_state_survives_across_runs(tmp_path):
    path = tmp_path / "state.json"
    first = RiskManager.from_state_file(path, max_drawdown_pct=0.10)
    first.size_order({"SPY": 1.0}, equity=100_000)
    first.save_state(path)

    second = RiskManager.from_state_file(path, max_drawdown_pct=0.10)
    assert second.peak_equity == 100_000
    assert second.size_order({"SPY": 1.0}, equity=85_000).decision == Decision.HALT


def test_halted_state_persists_across_runs(tmp_path):
    path = tmp_path / "state.json"
    first = RiskManager.from_state_file(path, max_drawdown_pct=0.10)
    first.size_order({"SPY": 1.0}, equity=100_000)
    first.size_order({"SPY": 1.0}, equity=80_000)
    first.save_state(path)

    second = RiskManager.from_state_file(path, max_drawdown_pct=0.10)
    assert second.halted
    assert second.size_order({"SPY": 1.0}, equity=100_000).decision == Decision.HALT


def test_missing_state_file_starts_fresh(tmp_path):
    rm = RiskManager.from_state_file(tmp_path / "nope.json")
    assert rm.peak_equity is None
    assert not rm.halted


def test_rejects_invalid_max_drawdown():
    with pytest.raises(ValueError):
        RiskManager(max_drawdown_pct=1.5)
