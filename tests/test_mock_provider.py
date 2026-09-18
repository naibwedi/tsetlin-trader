from tsetlin_trader.signal.mock_provider import MockSignalProvider


def test_returns_valid_signal():
    signal = MockSignalProvider().get_current_signal()

    assert 0.0 <= signal.confidence <= 1.0
    assert sum(signal.target_weights.values()) <= 1.0
    assert len(signal.rule_trace) > 0
    assert signal.strategy


def test_deterministic_for_same_week():
    provider = MockSignalProvider()
    first = provider.get_current_signal()
    second = provider.get_current_signal()

    assert first.strategy == second.strategy
    assert first.target_weights == second.target_weights
