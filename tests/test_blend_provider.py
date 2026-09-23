from datetime import date

from logic_alpha_tm.data import synthetic_prices

from tsetlin_trader.signal.blend_provider import BlendSignalProvider


def test_blend_signal_is_deterministic_and_fully_explained(tmp_path):
    path = tmp_path / "prices.csv"
    prices = synthetic_prices()
    prices.rename_axis("date").to_csv(path)
    provider = BlendSignalProvider(path, max_data_age_days=10000)
    first = provider.get_current_signal()
    second = provider.get_current_signal()
    assert first.target_weights == second.target_weights
    assert first.strategy == "blend"
    assert set(first.target_weights) <= {"SPY", "QQQ", "IWM", "TLT"}
    assert sum(first.target_weights.values()) <= 1
    assert len(first.rule_trace) == 4
