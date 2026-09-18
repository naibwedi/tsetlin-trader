import numpy as np
import pandas as pd
import pytest

from tsetlin_trader import portfolios


@pytest.fixture
def prices():
    idx = pd.bdate_range("2024-01-01", periods=150)
    base = np.linspace(100, 120, 150)
    return pd.DataFrame({"SPY": base, "QQQ": base * 1.1, "IWM": base * 0.9, "TLT": np.linspace(100, 90, 150)}, index=idx)


def d(prices, i):
    return prices.index[i].date().isoformat()


def test_first_step_initialises_and_buys(prices, tmp_path):
    path = tmp_path / "p.json"
    out = portfolios.step(prices, {"a": {"SPY": 1.0}, "b": {}}, d(prices, 100), path)

    assert out["changed"]
    assert out["portfolios"]["a"]["value"] == pytest.approx(1 - 0.0002)  # 2 bps on 100% turnover
    assert out["portfolios"]["b"]["value"] == 1.0


def test_marks_to_market_on_the_same_prices(prices, tmp_path):
    path = tmp_path / "p.json"
    portfolios.step(prices, {"a": {"SPY": 1.0}, "cash": {}}, d(prices, 100), path, cost_bps=0)
    out = portfolios.step(prices, {"a": {"SPY": 1.0}, "cash": {}}, d(prices, 110), path, cost_bps=0)

    expected = float(prices.SPY.iloc[110] / prices.SPY.iloc[100])
    assert out["portfolios"]["a"]["value"] == pytest.approx(expected)
    assert out["portfolios"]["cash"]["value"] == 1.0


def test_no_turnover_means_no_cost(prices, tmp_path):
    path = tmp_path / "p.json"
    portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 100), path)
    v1 = portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 101), path)["portfolios"]["a"]["value"]
    ratio = float(prices.SPY.iloc[101] / prices.SPY.iloc[100])
    assert v1 == pytest.approx((1 - 0.0002) * ratio)


def test_same_date_twice_is_a_noop(prices, tmp_path):
    path = tmp_path / "p.json"
    portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 100), path)
    out = portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 100), path)
    assert not out["changed"]


def test_going_backwards_is_rejected(prices, tmp_path):
    path = tmp_path / "p.json"
    portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 100), path)
    with pytest.raises(ValueError, match="before"):
        portfolios.step(prices, {"a": {"SPY": 1.0}}, d(prices, 99), path)


def test_build_targets_has_three_portfolios(prices):
    t = portfolios.build_targets(prices, {"SPY": 1.0})
    assert set(t) == {"blend", "blend_filtered", "tm"}
    assert sum(t["blend"].values()) == pytest.approx(1.0)
    assert t["tm"] == {"SPY": 1.0}
    assert all(0 <= w <= 1 for w in t["blend_filtered"].values())


def test_filter_halves_the_blend_under_stress(prices, monkeypatch):
    monkeypatch.setattr(portfolios, "stress", lambda p: True)
    t = portfolios.build_targets(prices, {})
    for s, w in t["blend"].items():
        assert t["blend_filtered"][s] == pytest.approx(w * 0.5, abs=1e-6)
