"""Isolated, after-close intraday paper replay. Never connects to a trading endpoint.

Each session enters at the 10:30 New York bar close and exits at the 15:55
bar close. Features use bars available at entry; training uses prior sessions.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd


def fetch_bars(start: str, end: str, output: Path, symbol: str = "SPY") -> int:
    """Download IEX 5-minute bars using paper keys; no order API is used."""
    if symbol != "SPY":
        raise ValueError("This frozen trial supports SPY only")
    key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"]
    rows: list[dict] = []
    token = None
    while True:
        params = {"timeframe": "5Min", "start": start, "end": end,
                  "feed": "iex", "limit": 10000, "sort": "asc"}
        if token:
            params["page_token"] = token
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars?{urlencode(params)}"
        request = Request(url, headers={"APCA-API-KEY-ID": key,
                                        "APCA-API-SECRET-KEY": secret})
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
        rows.extend(payload.get("bars", []))
        token = payload.get("next_page_token")
        if not token:
            break
    output.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"]).to_csv(output, index=False)
    return len(rows)


def sessions(bars: pd.DataFrame) -> pd.DataFrame:
    required = {"t", "o", "h", "l", "c", "v"}
    if not required.issubset(bars):
        raise ValueError(f"bars need columns {sorted(required)}")
    frame = bars.copy()
    frame["t"] = pd.to_datetime(frame["t"], utc=True).dt.tz_convert("America/New_York")
    frame = frame.sort_values("t")
    frame["day"] = frame.t.dt.date
    frame["clock"] = frame.t.dt.strftime("%H:%M")
    rows = []
    for day, group in frame.groupby("day", sort=True):
        # Alpaca bar timestamps mark the start of the interval. The 10:25
        # bar has completed at 10:30; 15:50 completes at 15:55.
        morning = group[(group.clock >= "09:30") & (group.clock <= "10:25")]
        exit_bar = group[group.clock == "15:50"]
        if len(morning) != 12 or len(exit_bar) != 1:
            continue  # incomplete or shortened session
        first, last = morning.iloc[0], morning.iloc[-1]
        entry = float(last.c)
        exit_price = float(exit_bar.iloc[0].c)
        if min(float(first.o), entry, exit_price) <= 0:
            continue
        rows.append({"day": str(day), "entry": entry, "exit": exit_price,
                     "morning_up": int(entry > float(first.o)),
                     "morning_range_high": int(float(morning.h.max()) / float(first.o) - 1 > .002),
                     "morning_range_low": int(float(morning.l.min()) / float(first.o) - 1 < -.002),
                     "morning_volume_high": int(float(morning.v.sum()) > 1_000_000),
                     "label": int(exit_price > entry)})
    return pd.DataFrame(rows)


FEATURES = ("morning_up", "morning_range_high", "morning_range_low", "morning_volume_high")


def replay(bars: pd.DataFrame, min_train: int = 30, cost_bps: float = 5.0) -> dict:
    if min_train < 10 or cost_bps < 0:
        raise ValueError("min_train must be >=10 and cost_bps must be nonnegative")
    data = sessions(bars)
    if len(data) <= min_train:
        return {"status": "insufficient_sessions", "sessions": len(data),
                "required": min_train + 1, "trades": []}
    from .signal.tm_model import TsetlinEnsemble

    value_tm = value_rule = 1.0
    trades = []
    for i in range(min_train, len(data)):
        prior = data.iloc[:i]
        today = data.iloc[i]
        # No fit on today's exit/label. Refit only as each prior day becomes known.
        if prior.label.nunique() < 2:
            tm_buy = False
            reason = "prior labels have only one class; stay in cash"
        else:
            model = TsetlinEnsemble(clauses=60, threshold=20, epochs=5, seeds=(1, 2))
            model.fit(prior[list(FEATURES)], prior.label)
            row = pd.DataFrame([{name: int(today[name]) for name in FEATURES}])
            votes = model.class_sums(row)
            tm_buy = int(model.classes_[int(np.argmax(votes))]) == 1
            pro, con = model.explain(row, 1 if tm_buy else 0, top_n=2)
            def clause(item):
                return {"vote": round(item.vote, 2), "literals": list(item.literals)}
            reason = {"votes": {str(k): round(float(v), 2) for k, v in zip(model.classes_, votes)},
                      "for": [clause(x) for x in pro], "against": [clause(x) for x in con]}
        rule_buy = bool(today.morning_up)
        gross = float(today.exit / today.entry - 1)
        net_tm = gross - 2 * cost_bps / 10000 if tm_buy else 0.0
        net_rule = gross - 2 * cost_bps / 10000 if rule_buy else 0.0
        value_tm *= 1 + net_tm
        value_rule *= 1 + net_rule
        trades.append({"day": today.day, "entry_time_et": "10:30", "exit_time_et": "15:55",
                       "tm_action": "buy_SPY" if tm_buy else "cash",
                       "baseline_action": "buy_SPY" if rule_buy else "cash",
                       "entry": round(float(today.entry), 4), "exit": round(float(today.exit), 4),
                       "tm_net_return": round(net_tm, 6), "baseline_net_return": round(net_rule, 6),
                       "tm_value": round(value_tm, 6), "baseline_value": round(value_rule, 6),
                       "explanation": reason})
    return {"status": "retrospective_paper_replay", "symbol": "SPY", "feed": "IEX",
            "cost_bps_per_side": cost_bps, "training_sessions": min_train,
            "sessions": len(data), "tm_final_value": round(value_tm, 6),
            "baseline_final_value": round(value_rule, 6), "trades": trades}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=Path, default=Path("data/spy-5min-iex.csv"))
    parser.add_argument("--output", type=Path, default=Path("results/intraday-trial.json"))
    parser.add_argument("--fetch-days", type=int, default=0,
                        help="Download this many recent calendar days of completed IEX bars")
    args = parser.parse_args()
    if args.fetch_days:
        if args.fetch_days < 45:
            parser.error("--fetch-days must be at least 45")
        end = datetime.now(timezone.utc).date()
        count = fetch_bars(str(end - timedelta(days=args.fetch_days)), str(end), args.bars)
        print(f"downloaded {count} bars to {args.bars}")
    result = replay(pd.read_csv(args.bars))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"{result['status']}: {result['sessions']} sessions; saved {args.output}")


if __name__ == "__main__":
    main()

