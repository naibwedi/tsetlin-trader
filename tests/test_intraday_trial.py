from datetime import date, timedelta

import pandas as pd

from tsetlin_trader.intraday_trial import replay, sessions


def bars(days=35):
    rows = []
    day = date(2026, 1, 5)
    while len({r["t"][:10] for r in rows}) < days:
        if day.weekday() < 5:
            for minute in range(12):
                stamp = pd.Timestamp(day.isoformat(), tz="America/New_York") + pd.Timedelta(hours=9, minutes=30 + minute * 5)
                price = 100 + (minute / 12 if day.day % 2 else -minute / 12)
                rows.append({"t": stamp.isoformat(), "o": price, "h": price + .1,
                             "l": price - .1, "c": price, "v": 100000})
            entry_stamp = pd.Timestamp(day.isoformat(), tz="America/New_York") + pd.Timedelta(hours=10, minutes=35)
            rows.append({"t": entry_stamp.isoformat(), "o": 100, "h": 102, "l": 99, "c": 100, "v": 100000})
            stamp = pd.Timestamp(day.isoformat(), tz="America/New_York") + pd.Timedelta(hours=15, minutes=45)
            rows.append({"t": stamp.isoformat(), "o": 101 if day.day % 3 else 99, "h": 102, "l": 99,
                         "c": 101 if day.day % 3 else 99, "v": 100000})
        day += timedelta(days=1)
    return pd.DataFrame(rows)


def test_incomplete_session_is_skipped():
    frame = bars(2)
    assert len(sessions(frame)) == 2
    assert len(sessions(frame.drop(frame.index[0]))) == 1


def test_replay_does_not_use_current_exit_for_signal(monkeypatch):
    class Model:
        classes_ = [0, 1]

        def __init__(self, **kwargs):
            pass

        def fit(self, x, y):
            self.last_y = int(y.iloc[-1])
            return self

        def class_sums(self, row):
            return [0, 1]

        def explain(self, row, label, top_n=2):
            return [], []

    monkeypatch.setattr("tsetlin_trader.signal.tm_model.TsetlinEnsemble", Model)
    original = bars()
    changed = original.copy()
    last = changed.index[-1]
    changed.loc[last, "o"] = 50
    first = replay(original, min_train=30)
    second = replay(changed, min_train=30)
    assert first["trades"][-1]["tm_action"] == second["trades"][-1]["tm_action"]
    assert first["trades"][-1]["tm_net_return"] != second["trades"][-1]["tm_net_return"]


def test_insufficient_data_stays_idle():
    result = replay(bars(3))
    assert result["status"] == "insufficient_sessions"
    assert result["trades"] == []


def test_real_tm_replay_produces_explained_trades():
    result = replay(bars(31), min_train=30)
    assert result["status"] == "retrospective_paper_replay"
    assert len(result["trades"]) == 1
    assert "votes" in result["trades"][0]["explanation"]

