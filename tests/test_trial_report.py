import json
from datetime import date, timedelta

from tsetlin_trader.trial_report import build_report, publish


def record(day, strategy="cash", value=1.0, status="executed", plan_only=False):
    return {
        "logged_at": f"{day}T20:00:00+00:00",
        "broker_provider": "alpaca", "signal_provider": "logic_alpha",
        "status": status, "plan_only": plan_only,
        "equity": 123456.78, "positions_before": {"SPY": 5000},
        "signal": {"as_of": day, "strategy": strategy, "target_weights": {},
                   "rule_trace": ["model=tmu", "selected cash"]},
        "orders": [{"symbol": "SPY", "side": "sell", "status": "OrderStatus.PENDING_NEW",
                    "order_id": "private-broker-id"}],
        "virtual_portfolios": {"as_of": day, "portfolios": {
            "tm": {"value": value}, "blend": {"value": 1.0},
            "blend_filtered": {"value": 1.0}}},
    }


def test_feed_tracks_distinct_forward_dates_without_exposing_account_data():
    first = record("2026-09-17")
    preview = record("2026-09-18", "momentum", 1.1, "planned", True)
    second = record("2026-09-24", "trend", 1.02)
    duplicate = record("2026-09-24", "trend", 1.03)
    report = build_report([first, preview, second, duplicate])

    assert len(report["points"]) == 2
    assert report["points"][-1]["tm"] == 1.03
    assert report["latest_signal"]["strategy"] == "trend"
    assert report["latest_signal"]["previous_strategy"] == "cash"
    assert report["gate"]["status"] == "collecting_evidence"
    serialized = json.dumps(report)
    assert "123456" not in serialized
    assert "private-broker-id" not in serialized


def test_failed_run_is_visible_without_pretending_there_is_a_new_signal():
    report = build_report([record("2026-09-17"), {"logged_at": "2026-09-24T20:00:00+00:00",
                                                 "broker_provider": "alpaca",
                                                 "status": "failed", "error": "feed down"}])
    assert report["latest_run"]["status"] == "failed"
    assert report["latest_signal"]["as_of"] == "2026-09-17"
    assert len(report["points"]) == 1


def test_publish_writes_json_without_private_log_fields(tmp_path):
    log = tmp_path / "decisions.jsonl"
    output = tmp_path / "live.json"
    log.write_text(json.dumps(record("2026-09-17")) + "\n", encoding="utf-8")
    publish(log, output)
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["latest_signal"]["strategy"] == "cash"
    assert "equity" not in output.read_text(encoding="utf-8")


def test_frozen_gate_needs_a_year_and_reports_both_outcomes():
    start = date(2026, 9, 17)
    rows = []
    for week in range(54):
        day = (start + timedelta(weeks=week)).isoformat()
        row = record(day, value=1.01 ** week)
        row["virtual_portfolios"]["portfolios"]["blend"]["value"] = 1.002 ** week
        row["virtual_portfolios"]["portfolios"]["blend_filtered"]["value"] = 1.003 ** week
        rows.append(row)

    assert build_report(rows[:30])["gate"]["status"] == "collecting_evidence"
    passed = build_report(rows)["gate"]
    assert passed["status"] == "performance_gate_met_execution_unverified"
    assert all(bound > 0 for bound in passed["lower_weekly_excess_95"].values())

    for week, row in enumerate(rows):
        row["virtual_portfolios"]["portfolios"]["tm"]["value"] = 1.001 ** week
    assert build_report(rows)["gate"]["status"] == "performance_gate_not_met"

