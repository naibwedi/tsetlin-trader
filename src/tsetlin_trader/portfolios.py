"""Three virtual portfolios tracked side by side, with identical execution rules.

    blend           equal mix of the trend, momentum and defensive strategies
    blend_filtered  the blend, halved when the simple stress rule fires
    tm              whatever the Tsetlin Machine signal says

Each is marked to market on the same adjusted closes, rebalanced at the same
time, and charged the same cost per dollar traded. None of them touches the
broker: this is bookkeeping so the models can be compared on returns rather
than on signals alone. The blend and the stress rule follow
logic_alpha_tm.risk_audit exactly. Cash earns zero.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .signal.logic_alpha_provider import strategy_target_weights

STATE_PATH = Path("results/portfolios.json")
REDUCTION = 0.5


def blend_weights(prices: pd.DataFrame) -> dict[str, float]:
    total: dict[str, float] = {}
    for strategy in ("trend", "momentum", "defensive"):
        for symbol, weight in strategy_target_weights(strategy, prices)[0].items():
            total[symbol] = total.get(symbol, 0.0) + weight / 3.0
    return {s: round(w, 6) for s, w in total.items()}


def stress(prices: pd.DataFrame) -> bool:
    daily = prices.SPY.pct_change()
    down = float(prices.SPY.pct_change(60).iloc[-1]) < 0
    jumpy = float(daily.rolling(20).std().iloc[-1]) > float(daily.rolling(60).std().iloc[-1])
    return bool(down and jumpy)


def build_targets(prices: pd.DataFrame, tm_weights: dict[str, float]) -> dict[str, dict[str, float]]:
    blend = blend_weights(prices)
    scale = REDUCTION if stress(prices) else 1.0
    return {
        "blend": blend,
        "blend_filtered": {s: round(w * scale, 6) for s, w in blend.items()},
        "tm": dict(tm_weights),
    }


def step(
    prices: pd.DataFrame,
    targets: dict[str, dict[str, float]],
    as_of: str,
    state_path: Path = STATE_PATH,
    cost_bps: float = 2.0,
) -> dict:
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {"started": as_of, "as_of": None, "portfolios": {}}

    prev = state["as_of"]
    if prev is not None and pd.Timestamp(as_of) < pd.Timestamp(prev):
        raise ValueError(f"as_of {as_of} is before the last recorded date {prev}")
    if prev == as_of:
        return _summary(state, changed=False)

    for name, target in targets.items():
        book = state["portfolios"].setdefault(name, {"value": 1.0, "cash": 1.0, "holdings": {}})
        holdings = dict(book["holdings"])
        if prev is not None:
            for symbol in holdings:
                holdings[symbol] *= float(prices.loc[as_of, symbol] / prices.loc[prev, symbol])
        value = float(book["cash"]) + sum(holdings.values())

        wanted = {s: value * w for s, w in target.items() if w > 0}
        turnover = sum(abs(wanted.get(s, 0.0) - holdings.get(s, 0.0)) for s in set(wanted) | set(holdings))
        value -= turnover * cost_bps / 10_000

        book["value"] = value
        book["holdings"] = {s: value * w for s, w in target.items() if w > 0}
        book["cash"] = value - sum(book["holdings"].values())

    state["as_of"] = as_of
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return _summary(state, changed=True)


def _summary(state: dict, changed: bool) -> dict:
    out = {"started": state["started"], "as_of": state["as_of"], "changed": changed, "portfolios": {}}
    for name, book in state["portfolios"].items():
        out["portfolios"][name] = {
            "value": round(book["value"], 6),
            "return_since_start": round(book["value"] - 1.0, 6),
            "holdings": {s: round(v, 4) for s, v in book["holdings"].items()},
        }
    return out
