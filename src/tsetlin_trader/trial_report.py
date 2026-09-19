"""Publish a sanitized, reproducible forward-trial feed for docs/index.html.

This is an evidence report, not a trading signal or permission to use real money.
The frozen gate compares paired weekly net returns from the three virtual books.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

LOG_PATH = Path("results/decisions.jsonl")
REPORT_PATH = Path("docs/live.json")
MIN_INTERVALS = 52
MIN_CALENDAR_DAYS = 365
BLOCK_WEEKS = 4
BOOTSTRAP_SAMPLES = 2000


def _drawdown(values: list[float]) -> float:
    peak = 1.0
    worst = 0.0
    for value in values:
        peak = max(peak, value)
        worst = max(worst, 1.0 - value / peak)
    return worst


def _lower_mean_bound(excess: list[float], seed: int) -> float:
    """One-sided 95% moving-block bootstrap bound for mean weekly excess."""
    n = len(excess)
    rng = random.Random(seed)
    block = min(BLOCK_WEEKS, n)
    means = []
    for _ in range(BOOTSTRAP_SAMPLES):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n - block + 1)
            sample.extend(excess[start : start + block])
        means.append(sum(sample[:n]) / n)
    means.sort()
    return means[int(0.05 * BOOTSTRAP_SAMPLES)]


def _latest_run(records: list[dict]) -> dict | None:
    if not records:
        return None
    row = next((record for record in reversed(records)
                if record.get("broker_provider") == "alpaca"), None)
    if row is None:
        return None
    status = row.get("status", "unknown")
    return {
        "logged_at": row.get("logged_at"),
        "status": status,
        "plan_only": bool(row.get("plan_only", False)),
        "reason": "Run failed; inspect the private logs" if status == "failed" else None,
    }


def _latest_signal(records: list[dict]) -> dict | None:
    for row in reversed(records):
        signal = row.get("signal")
        if (row.get("broker_provider") == "alpaca"
                and row.get("signal_provider") == "logic_alpha"
                and row.get("status") in ("executed", "execution_incomplete")
                and not row.get("plan_only") and isinstance(signal, dict)):
            previous = next((other["signal"]["strategy"] for other in reversed(records)
                             if other.get("broker_provider") == "alpaca"
                             and other.get("signal_provider") == "logic_alpha"
                             and other.get("status") == "executed"
                             and not other.get("plan_only")
                             and isinstance(other.get("signal"), dict)
                             and other["signal"].get("as_of") != signal.get("as_of")), None)
            return {
                "logged_at": row.get("logged_at"),
                "as_of": signal.get("as_of"),
                "strategy": signal.get("strategy"),
                "previous_strategy": previous,
                "target_weights": signal.get("target_weights", {}),
                "rule_trace": signal.get("rule_trace", []),
                "risk_decision": row.get("risk_decision"),
                "risk_reason": row.get("risk_reason"),
                "orders": [{"symbol": order.get("symbol"), "side": order.get("side"),
                            "status": order.get("status")}
                           for order in row.get("orders", [])],
            }
    return None


def _trial_points(records: list[dict]) -> list[dict]:
    by_date = {}
    for row in records:
        book = row.get("virtual_portfolios")
        if (row.get("status") != "executed" or row.get("plan_only")
                or row.get("broker_provider") != "alpaca"
                or row.get("signal_provider") != "logic_alpha"
                or not isinstance(book, dict)):
            continue
        portfolios = book.get("portfolios", {})
        as_of = book.get("as_of")
        if not as_of or not all(name in portfolios for name in ("tm", "blend", "blend_filtered")):
            continue
        by_date[as_of] = {
            "as_of": as_of,
            "tm": float(portfolios["tm"]["value"]),
            "blend": float(portfolios["blend"]["value"]),
            "blend_filtered": float(portfolios["blend_filtered"]["value"]),
        }
    by_week = {}
    for key in sorted(by_date):
        day = date.fromisoformat(key)
        by_week[day.isocalendar()[:2]] = by_date[key]
    return [by_week[key] for key in sorted(by_week)]


def _gate(points: list[dict]) -> dict:
    intervals = max(0, len(points) - 1)
    days = (date.fromisoformat(points[-1]["as_of"]) - date.fromisoformat(points[0]["as_of"])).days if points else 0
    max_gap = max((date.fromisoformat(right["as_of"]) - date.fromisoformat(left["as_of"])).days
                  for left, right in zip(points, points[1:])) if intervals else 0
    gate = {
        "status": "collecting_evidence",
        "intervals": intervals,
        "calendar_days": days,
        "minimum_intervals": MIN_INTERVALS,
        "minimum_calendar_days": MIN_CALENDAR_DAYS,
        "maximum_gap_days": max_gap,
        "criteria": "TM must beat both simple books on net return; its 95% moving-block bootstrap lower bound for paired weekly excess return must exceed zero against each; maximum drawdown must be no worse than the blend.",
        "execution_verified": False,
    }
    if intervals < MIN_INTERVALS or days < MIN_CALENDAR_DAYS:
        return gate
    if max_gap > 14:
        gate["status"] = "trial_has_missing_weeks"
        return gate

    values = {name: [point[name] for point in points] for name in ("tm", "blend", "blend_filtered")}
    weekly = {name: [next_value / value - 1 for value, next_value in zip(series, series[1:])]
              for name, series in values.items()}
    lower = {name: _lower_mean_bound([tm - base for tm, base in zip(weekly["tm"], weekly[name])], seed)
             for seed, name in enumerate(("blend", "blend_filtered"), start=7)}
    passed = (all(values["tm"][-1] > values[name][-1] and lower[name] > 0
                  for name in ("blend", "blend_filtered"))
              and _drawdown(values["tm"]) <= _drawdown(values["blend"]))
    gate.update(
        status="performance_gate_met_execution_unverified" if passed else "performance_gate_not_met",
        lower_weekly_excess_95=lower,
        maximum_drawdown={name: _drawdown(series) for name, series in values.items()},
    )
    return gate


def build_report(records: list[dict]) -> dict:
    points = _trial_points(records)
    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "latest_run": _latest_run(records),
        "latest_signal": _latest_signal(records),
        "points": points,
        "gate": _gate(points),
        "notes": [
            "Virtual books use adjusted closing prices and a 2 bps trading-cost assumption; they are not broker fills.",
            "Orders show the status logged at submission. A pending order is not a confirmed fill.",
            "Passing a research gate would trigger review, never automatic real-money trading.",
        ],
    }


def publish(log_path: Path = LOG_PATH, report_path: Path = REPORT_PATH) -> dict:
    records = []
    if log_path.exists():
        with log_path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    records.append(json.loads(line))
    report = build_report(records)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    return report


if __name__ == "__main__":
    publish()

