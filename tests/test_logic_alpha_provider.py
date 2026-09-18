import pandas as pd
import pytest
from logic_alpha_tm.data import synthetic_prices

from tsetlin_trader.signal.logic_alpha_provider import LogicAlphaProvider, strategy_target_weights

STRATEGIES = {"trend", "momentum", "defensive", "cash"}


@pytest.fixture
def prices_csv(tmp_path):
    path = tmp_path / "prices.csv"
    synthetic_prices().rename_axis("date").to_csv(path)
    return path


def make_provider(path, **kwargs):
    return LogicAlphaProvider(prices_csv=path, max_data_age_days=10_000, **kwargs)


def test_produces_a_valid_signal_from_the_real_model(prices_csv):
    signal = make_provider(prices_csv).get_current_signal()

    assert signal.strategy in STRATEGIES
    assert 0.0 <= signal.confidence <= 1.0
    assert sum(signal.target_weights.values()) <= 1.0
    assert signal.as_of == synthetic_prices().index[-1].date().isoformat()
    assert any("evidence for" in line for line in signal.rule_trace)


def test_signal_is_deterministic(prices_csv):
    first = make_provider(prices_csv).get_current_signal()
    second = make_provider(prices_csv).get_current_signal()

    assert first.strategy == second.strategy
    assert first.target_weights == second.target_weights


def test_signal_date_tracks_last_available_price(prices_csv, tmp_path):
    full = pd.read_csv(prices_csv, parse_dates=["date"]).set_index("date")
    cut = full.iloc[:-30]
    cut_path = tmp_path / "cut.csv"
    cut.to_csv(cut_path)

    truncated = make_provider(cut_path).get_current_signal()
    assert truncated.as_of == cut.index[-1].date().isoformat()


def test_refuses_stale_data(prices_csv):
    provider = LogicAlphaProvider(prices_csv=prices_csv, max_data_age_days=5)
    with pytest.raises(RuntimeError, match="stale"):
        provider.get_current_signal()


def test_refuses_too_little_history(tmp_path):
    path = tmp_path / "short.csv"
    synthetic_prices(n=400).rename_axis("date").to_csv(path)

    with pytest.raises(RuntimeError, match="labelled days"):
        make_provider(path).get_current_signal()


def test_strategy_weights_follow_the_research_rules():
    prices = synthetic_prices()

    for strategy in STRATEGIES:
        weights, note = strategy_target_weights(strategy, prices)
        assert set(weights) <= {"SPY", "QQQ", "IWM", "TLT"}
        assert note

    assert strategy_target_weights("cash", prices)[0] == {}
    with pytest.raises(ValueError):
        strategy_target_weights("nonsense", prices)
