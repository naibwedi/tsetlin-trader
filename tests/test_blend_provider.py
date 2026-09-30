from tsetlin_trader.signal.blend_provider import BlendSignalProvider


def write_prices(path, n=300):
    import datetime as dt
    rows = ["date,SPY,QQQ,IWM,TLT"]
    day, values = dt.date.today() - dt.timedelta(days=450), [100.0] * 4
    while len(rows) <= n:
        if day.weekday() < 5:
            values = [v * (1 + rate) for v, rate in zip(values, (.001, .0012, .0008, .0002))]
            rows.append(f"{day.isoformat()}," + ",".join(str(v) for v in values))
        day += dt.timedelta(days=1)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def test_blend_signal_is_deterministic_and_fully_explained(tmp_path):
    path = tmp_path / "prices.csv"
    write_prices(path)
    provider = BlendSignalProvider(path, max_data_age_days=10000)
    first = provider.get_current_signal()
    second = provider.get_current_signal()
    assert first.target_weights == second.target_weights
    assert first.strategy == "blend"
    assert set(first.target_weights) <= {"SPY", "QQQ", "IWM", "TLT"}
    assert sum(first.target_weights.values()) <= 1
    assert len(first.rule_trace) == 4
