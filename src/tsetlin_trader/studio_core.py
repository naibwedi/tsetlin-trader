"""Pure-Python market rules and backtest engine for the local studio.

No pandas or numpy, so it runs even where native extensions are blocked. It is
a second, independent implementation of the rules in `logic_alpha_tm.risk_audit`
(blend, stress filter, and the cost-aware account model). It is checked against
that engine's published numbers in `tests/test_studio_core.py` and by
`python -m tsetlin_trader.studio_core --validate <csv>`; where the two ever
disagree, the research package is the authority.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

ASSETS = ("SPY", "QQQ", "IWM", "TLT")
LAG = 2
REDUCTION = 0.5
MOMENTUM_UNIVERSE = ("SPY", "QQQ", "IWM")


def load_prices_csv(path: str | Path) -> tuple[list[str], dict[str, list[float]]]:
    """Read a wide adjusted-close CSV (date,SPY,QQQ,IWM,TLT) with the research repo's validation."""
    dates: list[str] = []
    cols: dict[str, list[float]] = {a: [] for a in ASSETS}
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [a for a in ("date", *ASSETS) if a not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"Missing required price columns: {missing}")
        for row in reader:
            dates.append(row["date"][:10])
            for a in ASSETS:
                value = float(row[a])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(f"Prices must be positive and complete ({row['date']} {a})")
                cols[a].append(value)
    if any(b <= a for a, b in zip(dates, dates[1:])):
        raise ValueError("Dates must be unique and increasing")
    if not dates:
        raise ValueError("No price rows")
    return dates, cols


def sma(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= window:
            total -= values[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def pct_change(values: list[float], window: int) -> list[float | None]:
    return [values[i] / values[i - window] - 1.0 if i >= window else None for i in range(len(values))]


def rolling_std(returns: list[float | None], window: int) -> list[float | None]:
    """Sample standard deviation (ddof=1) over a full window, like pandas rolling().std()."""
    out: list[float | None] = [None] * len(returns)
    for i in range(window, len(returns)):
        chunk = returns[i - window + 1: i + 1]
        if any(x is None for x in chunk):
            continue
        mean = sum(chunk) / window
        out[i] = math.sqrt(sum((x - mean) ** 2 for x in chunk) / (window - 1))
    return out


def daily_returns(values: list[float]) -> list[float | None]:
    return [None] + [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _zero() -> dict[str, float]:
    return {a: 0.0 for a in ASSETS}


def target_weights(prices: dict[str, list[float]]) -> dict[str, list[dict[str, float]]]:
    """Targets known after each day's close, before any execution delay."""
    spy = prices["SPY"]
    n = len(spy)
    ma20, ma100 = sma(spy, 20), sma(spy, 100)
    ret60 = {a: pct_change(prices[a], 60) for a in MOMENTUM_UNIVERSE}
    out: dict[str, list[dict[str, float]]] = {k: [] for k in ("trend", "momentum", "defensive", "blend")}
    for t in range(n):
        trend, momentum, defensive = _zero(), _zero(), _zero()
        if ma20[t] is not None and ma100[t] is not None and ma20[t] > ma100[t]:
            trend["SPY"] = 1.0
        scores = [ret60[a][t] for a in MOMENTUM_UNIVERSE]
        if all(s is not None for s in scores):
            best = max(range(len(scores)), key=lambda i: scores[i])  # first maximum, like idxmax
            momentum[MOMENTUM_UNIVERSE[best]] = 1.0
        if ma100[t] is not None:
            if spy[t] > ma100[t]:
                defensive["SPY"] = 1.0
            else:
                defensive["TLT"] = 1.0
        out["trend"].append(trend)
        out["momentum"].append(momentum)
        out["defensive"].append(defensive)
        out["blend"].append({a: (trend[a] + momentum[a] + defensive[a]) / 3.0 for a in ASSETS})
    return out


def stress_flags(prices: dict[str, list[float]]) -> list[bool]:
    """SPY down over 60 days AND 20-day volatility above 60-day volatility."""
    spy = prices["SPY"]
    r60 = pct_change(spy, 60)
    daily = daily_returns(spy)
    s20, s60 = rolling_std(daily, 20), rolling_std(daily, 60)
    return [
        r60[t] is not None and r60[t] < 0 and s20[t] is not None and s60[t] is not None and s20[t] > s60[t]
        for t in range(len(spy))
    ]


def scale(targets: list[dict[str, float]], factors: list[float]) -> list[dict[str, float]]:
    return [{a: w[a] * f for a in ASSETS} for w, f in zip(targets, factors)]


def account(
    prices: dict[str, list[float]],
    targets: list[dict[str, float]],
    lag: int = LAG,
    cost_bps: float = 2.0,
) -> dict[str, list[float]]:
    """Daily target-weight rebalancing. A target formed after close t is filled at
    close t+1 and first earns the return ending at t+2 (lag=2). Costs are charged
    on turnover measured against drifted weights. Cash earns zero."""
    n = len(prices["SPY"])
    rets = {a: [0.0] + [prices[a][t] / prices[a][t - 1] - 1.0 for t in range(1, n)] for a in ASSETS}
    zero = _zero()
    weights = [targets[t - lag] if t >= lag else zero for t in range(n)]
    for w in weights:
        if any(w[a] < -1e-12 for a in ASSETS) or sum(w.values()) > 1 + 1e-10:
            raise ValueError("Only unlevered, long-only weights are supported")
    gross = [sum(weights[t][a] * rets[a][t] for a in ASSETS) for t in range(n)]
    net, turnover, exposure, cost = [], [], [], []
    for t in range(n):
        if t == 0:
            drifted = zero
        else:
            denom = 1.0 + gross[t - 1]
            drifted = {a: weights[t - 1][a] * (1.0 + rets[a][t - 1]) / denom for a in ASSETS}
        turn = sum(abs(weights[t][a] - drifted[a]) for a in ASSETS)
        c = turn * cost_bps / 10_000.0
        net.append((1.0 - c) * (1.0 + gross[t]) - 1.0)
        turnover.append(turn)
        cost.append(c)
        exposure.append(sum(weights[t].values()))
    return {"return": net, "gross": gross, "turnover": turnover, "cost": cost, "exposure": exposure}


def metrics(values: list[float]) -> dict[str, float]:
    """Same definitions as logic_alpha_tm.risk_audit.audit_metrics."""
    n = len(values)
    if not n or not all(math.isfinite(v) for v in values):
        raise ValueError("Metrics require finite, nonempty returns")
    wealth = 1.0
    peak = 1.0
    mdd = 0.0
    for v in values:
        wealth *= 1.0 + v
        peak = max(peak, wealth)
        mdd = min(mdd, wealth / peak - 1.0)
    mean_daily = sum(values) / n
    vol = math.sqrt(sum((v - mean_daily) ** 2 for v in values) / (n - 1)) * math.sqrt(252) if n > 1 else 0.0
    mean = mean_daily * 252
    downside = math.sqrt(sum(min(v, 0.0) ** 2 for v in values) / n) * math.sqrt(252)
    return {
        "cagr": wealth ** (252 / n) - 1.0,
        "annual_volatility": vol,
        "sharpe": mean / vol if vol else 0.0,
        "sortino": mean / downside if downside else 0.0,
        "max_drawdown": mdd,
        "total_return": wealth - 1.0,
    }


def curve(values: list[float]) -> list[float]:
    out, wealth = [], 1.0
    for v in values:
        wealth *= 1.0 + v
        out.append(wealth)
    return out


def portfolios(prices: dict[str, list[float]]) -> dict[str, list[dict[str, float]]]:
    """The comparison portfolios used by the research audit."""
    t = target_weights(prices)
    n = len(prices["SPY"])
    stress = stress_flags(prices)
    spy = [dict(_zero(), SPY=1.0) for _ in range(n)]
    return {
        "blend": t["blend"],
        "simple_risk_filter": scale(t["blend"], [REDUCTION if s else 1.0 for s in stress]),
        "constant_half": scale(t["blend"], [REDUCTION] * n),
        "always_defensive": t["defensive"],
        "SPY": spy,
    }


def evaluate(
    prices: dict[str, list[float]],
    first_return: int,
    last_return: int | None = None,
    cost_bps: float = 2.0,
) -> dict[str, dict]:
    """Run every comparison portfolio over returns[first_return : last_return+1]."""
    out = {}
    for name, targets in portfolios(prices).items():
        ledger = account(prices, targets, LAG, cost_bps)
        end = None if last_return is None else last_return + 1
        r = ledger["return"][first_return:end]
        e = ledger["exposure"][first_return:end]
        tv = ledger["turnover"][first_return:end]
        out[name] = {
            **metrics(r),
            "mean_exposure": sum(e) / len(e),
            "annual_turnover": sum(tv) / len(tv) * 252,
            "returns": r,
        }
    return out


def first_return_index(dates: list[str], start: str = "2008-01-01", min_train: int = 504,
                       feature_warmup: int = 100, lag: int = LAG) -> int:
    """Position of the first scored return in the research audit: the first trading day on or
    after `start` (but never before the model's warm-up and minimum training window), plus the
    execution lag."""
    pos = next((i for i, d in enumerate(dates) if d >= start), len(dates) - 1)
    return max(pos, feature_warmup + min_train) + lag


def _validate(csv_path: str) -> None:
    dates, prices = load_prices_csv(csv_path)
    first = first_return_index(dates)
    res = evaluate(prices, first, cost_bps=2.0)
    reference = {
        "blend": ("equal_weight", 0.0991, 0.783, -0.2056, 0.915),
        "constant_half": ("constant_half", 0.0507, 0.782, -0.1071, 0.457),
        "always_defensive": ("always_defensive", 0.1080, 0.751, -0.2004, 1.0),
        "SPY": ("SPY", 0.0980, 0.551, -0.5186, 1.0),
        "simple_risk_filter": ("simple_risk_filter", 0.0924, 0.783, -0.1444, 0.854),
    }
    print(f"data {dates[0]} .. {dates[-1]} ({len(dates)} rows); scored from {dates[first]}")
    print(f"{'portfolio':<20}{'cagr':>16}{'sharpe':>16}{'max dd':>18}{'exposure':>16}")
    for key, (label, cagr, sharpe, mdd, expo) in reference.items():
        r = res[key]
        print(f"{label:<20}{r['cagr']*100:7.2f}% ({cagr*100:5.2f}){r['sharpe']:8.3f} ({sharpe:5.3f}){r['max_drawdown']*100:9.2f}% ({mdd*100:6.2f}){r['mean_exposure']*100:8.1f}% ({expo*100:5.1f})")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate", metavar="CSV", help="compare against the research v0.3 published table")
    args = parser.parse_args()
    if args.validate:
        _validate(args.validate)
