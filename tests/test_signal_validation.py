import math

import pytest

from tsetlin_trader.signal.base import Signal


def make(weights, confidence=None):
    return Signal(strategy="x", target_weights=weights, confidence=confidence, rule_trace=["t"])


def test_valid_signal():
    s = make({"SPY": 0.6, "TLT": 0.4})
    assert s.target_weights == {"SPY": 0.6, "TLT": 0.4}


def test_negative_weight_rejected_even_if_sum_is_fine():
    with pytest.raises(ValueError, match=">= 0"):
        make({"SPY": 1.5, "QQQ": -0.5})


def test_nan_and_inf_rejected():
    with pytest.raises(ValueError, match="finite"):
        make({"SPY": math.nan})
    with pytest.raises(ValueError, match="finite"):
        make({"SPY": math.inf})


def test_symbol_outside_universe_rejected():
    with pytest.raises(ValueError, match="outside the universe"):
        make({"AAPL": 1.0})


def test_leverage_rejected():
    with pytest.raises(ValueError, match="<= 1.0"):
        make({"SPY": 0.7, "QQQ": 0.7})


def test_confidence_range():
    with pytest.raises(ValueError):
        make({"SPY": 1.0}, confidence=1.5)
    assert make({"SPY": 1.0}, confidence=None).confidence is None
