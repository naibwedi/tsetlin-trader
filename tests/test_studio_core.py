import math
import statistics

import pytest

from tsetlin_trader import studio_core as core

A = core.ASSETS


def series(n, daily=0.01, start=100.0):
    out = [start]
    for _ in range(n - 1):
        out.append(out[-1] * (1 + daily))
    return out


def flat_prices(n, **overrides):
    p = {a: series(n, 0.0) for a in A}
    p.update(overrides)
    return p


def test_sma_needs_a_full_window():
    out = core.sma([1, 2, 3, 4, 5], 3)
    assert out[:2] == [None, None]
    assert out[2:] == [2.0, 3.0, 4.0]


def test_pct_change_matches_definition():
    out = core.pct_change([100, 110, 121], 1)
    assert out[0] is None
    assert out[1] == pytest.approx(0.10)
    assert out[2] == pytest.approx(0.10)


def test_rolling_std_is_the_sample_std():
    r = core.daily_returns([100, 101, 99, 103, 102, 105, 104, 108])
    out = core.rolling_std(r, 4)
    assert out[3] is None  # window still contains the missing first return
    assert out[4] == pytest.approx(statistics.stdev(r[1:5]))
    assert out[7] == pytest.approx(statistics.stdev(r[4:8]))


def test_metrics_known_values():
    m = core.metrics([0.10, -0.10])
    assert m["total_return"] == pytest.approx(1.1 * 0.9 - 1)
    assert m["max_drawdown"] == pytest.approx(-0.10)  # from 1.10 down to 0.99
    assert m["cagr"] == pytest.approx((1.1 * 0.9) ** (252 / 2) - 1)


def test_metrics_drawdown_counts_the_starting_wealth():
    assert core.metrics([-0.05, 0.02])["max_drawdown"] == pytest.approx(-0.05)


def test_metrics_reject_empty_and_nan():
    with pytest.raises(ValueError):
        core.metrics([])
    with pytest.raises(ValueError):
        core.metrics([0.01, float("nan")])


def test_account_charges_cost_only_on_the_first_fill_of_a_constant_target():
    n = 6
    p = flat_prices(n, SPY=series(n, 0.01))
    targets = [dict(zip(A, (1.0, 0.0, 0.0, 0.0))) for _ in range(n)]
    led = core.account(p, targets, lag=2, cost_bps=2.0)
    assert led["return"][0] == 0.0 and led["return"][1] == 0.0
    assert led["return"][2] == pytest.approx((1 - 0.0002) * 1.01 - 1)
    assert led["return"][3] == pytest.approx(0.01)  # weights drifted with the price: no turnover
    assert led["exposure"][1] == 0.0 and led["exposure"][2] == 1.0


def test_account_rejects_leverage_and_shorts():
    p = flat_prices(5)
    with pytest.raises(ValueError):
        core.account(p, [dict(zip(A, (0.7, 0.7, 0.0, 0.0)))] * 5)
    with pytest.raises(ValueError):
        core.account(p, [dict(zip(A, (1.5, -0.5, 0.0, 0.0)))] * 5)


def test_rules_in_a_rising_market():
    n = 220
    p = {"SPY": series(n, 0.003), "QQQ": series(n, 0.006), "IWM": series(n, 0.001), "TLT": series(n, 0.0)}
    t = core.target_weights(p)
    last = {k: v[-1] for k, v in t.items()}
    assert last["trend"]["SPY"] == 1.0
    assert last["momentum"]["QQQ"] == 1.0 and last["momentum"]["SPY"] == 0.0
    assert last["defensive"]["SPY"] == 1.0
    assert last["blend"]["SPY"] == pytest.approx(2 / 3)
    assert last["blend"]["QQQ"] == pytest.approx(1 / 3)
    assert sum(last["blend"].values()) == pytest.approx(1.0)


def test_rules_in_a_falling_market_go_defensive():
    n = 220
    p = {"SPY": series(n, -0.003), "QQQ": series(n, -0.004), "IWM": series(n, -0.005), "TLT": series(n, 0.001)}
    last = core.target_weights(p)["defensive"][-1]
    assert last["TLT"] == 1.0 and last["SPY"] == 0.0
    assert core.target_weights(p)["trend"][-1]["SPY"] == 0.0


def test_rules_are_flat_until_there_is_history():
    t = core.target_weights({a: series(50, 0.01) for a in A})
    assert all(sum(w.values()) == 0.0 for w in t["blend"])


def jumpy_decline():
    prices = series(80, 0.001)
    for i in range(20):
        prices.append(prices[-1] * (0.97 if i % 2 == 0 else 1.02))
    return prices


def test_stress_needs_a_falling_and_jumpier_market():
    prices = jumpy_decline()
    flags = core.stress_flags(flat_prices(len(prices), SPY=prices))
    assert flags[-1] is True
    assert flags[30] is False  # too early for a 60-day window
    assert flags[79] is False  # rising and calm
    assert core.stress_flags(flat_prices(120))[-1] is False


def test_simple_risk_filter_halves_the_blend_under_stress():
    prices = jumpy_decline()
    n = len(prices)
    p = {"SPY": prices, "QQQ": series(n, 0.0), "IWM": series(n, 0.0), "TLT": series(n, 0.0)}
    port = core.portfolios(p)
    blend, filt = sum(port["blend"][-1].values()), sum(port["simple_risk_filter"][-1].values())
    assert blend > 0
    assert filt == pytest.approx(blend * 0.5)


def test_first_return_index_matches_the_research_start():
    import datetime as dt

    dates, day = [], dt.date(2003, 1, 2)
    while len(dates) < 4000:
        if day.weekday() < 5:
            dates.append(day.isoformat())
        day += dt.timedelta(days=1)
    idx = core.first_return_index(dates, "2008-01-01")
    first_on_or_after = next(i for i, d in enumerate(dates) if d >= "2008-01-01")
    assert idx == first_on_or_after + core.LAG
    assert core.first_return_index(dates[:300], "2008-01-01") == 100 + 504 + core.LAG  # short history uses the floor


def test_load_prices_csv_validates(tmp_path):
    good = tmp_path / "g.csv"
    good.write_text("date,SPY,QQQ,IWM,TLT\n2020-01-02,1,2,3,4\n2020-01-03,1.1,2.1,3.1,4.1\n", encoding="utf-8")
    dates, cols = core.load_prices_csv(good)
    assert dates == ["2020-01-02", "2020-01-03"] and cols["TLT"][1] == 4.1

    missing = tmp_path / "m.csv"
    missing.write_text("date,SPY\n2020-01-02,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        core.load_prices_csv(missing)

    backwards = tmp_path / "b.csv"
    backwards.write_text("date,SPY,QQQ,IWM,TLT\n2020-01-03,1,1,1,1\n2020-01-02,1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="increasing"):
        core.load_prices_csv(backwards)

    bad = tmp_path / "n.csv"
    bad.write_text("date,SPY,QQQ,IWM,TLT\n2020-01-02,-1,1,1,1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        core.load_prices_csv(bad)


def test_matches_the_research_engine_on_synthetic_prices():
    """The studio's engine is an independent implementation. Where pandas is available
    it must agree with logic_alpha_tm to floating-point precision."""
    try:
        import pandas as pd
        from logic_alpha_tm import risk_audit as risk
        from logic_alpha_tm.data import synthetic_prices
    except ImportError as exc:  # includes native-extension load failures
        pytest.skip(f"research stack unavailable here: {exc}")

    df = synthetic_prices(n=900)
    prices = {a: df[a].tolist() for a in A}
    theirs = risk.target_weights(df)
    mine = core.target_weights(prices)
    for name in ("trend", "momentum", "defensive", "blend"):
        for a in A:
            assert mine[name][-1][a] == pytest.approx(float(theirs[name][a].iloc[-1]))
            diffs = [abs(m[a] - float(v)) for m, v in zip(mine[name], theirs[name][a])]
            assert max(diffs) < 1e-12

    cfg = risk.AuditConfig()
    ledger = risk.account(df, theirs["blend"], cfg)
    ours = core.account(prices, mine["blend"], cfg.execution_lag, cfg.cost_bps)
    assert max(abs(a - b) for a, b in zip(ours["return"], ledger["return"])) < 1e-12
    assert max(abs(a - b) for a, b in zip(ours["exposure"], ledger["exposure"])) < 1e-12

    daily = df.pct_change()
    stress = ((df.SPY.pct_change(60) < 0) & (daily.SPY.rolling(20).std() > daily.SPY.rolling(60).std())).tolist()
    assert core.stress_flags(prices) == stress

    values = pd.Series(ours["return"][300:])
    theirs_m = risk.audit_metrics(values)
    mine_m = core.metrics(ours["return"][300:])
    for k in ("cagr", "sharpe", "max_drawdown", "total_return", "annual_volatility", "sortino"):
        assert mine_m[k] == pytest.approx(theirs_m[k], rel=1e-9, abs=1e-12)
