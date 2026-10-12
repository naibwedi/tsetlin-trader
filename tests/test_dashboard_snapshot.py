import json
from pathlib import Path

from tsetlin_trader.broker.base import AccountSnapshot
from tsetlin_trader.dashboard_snapshot import _completed_trial_weeks, build_snapshot


class FakePaperBroker:
    def account_identity(self): return "paper-account"
    def get_account(self): return AccountSnapshot(100_100.0, 75_000.0, 150_000.0)
    def get_positions(self): return {"SPY": 16_700.0, "QQQ": 8_400.0}
    def open_order_symbols(self): return set()
    def get_order_status(self, order_id): return "filled"


def test_snapshot_is_sanitized_and_reconciled(tmp_path: Path, monkeypatch):
    state_dir = tmp_path / "private"
    state_dir.mkdir()
    (state_dir / "state.json").write_text(json.dumps({"peak_equity": 100_000.0, "halted": False}))
    (state_dir / "account.json").write_text(json.dumps({"account_id": "paper-account"}))
    decision = {
        "signal_provider": "blend",
        "status": "execution_pending",
        "signal": {"strategy": "blend", "as_of": "2026-10-01", "rule_trace": [
            "trend sleeve (1/3): target=SPY", "momentum sleeve (1/3): target=QQQ", "defensive sleeve (1/3): target=SPY"
        ]},
        "orders": [{"order_id": "one"}, {"order_id": "two"}],
    }
    (state_dir / "decisions.jsonl").write_text(json.dumps(decision) + "\n")
    monkeypatch.setenv("MAX_DRAWDOWN_PCT", "0.15")
    monkeypatch.setenv("POSITION_FRACTION", "0.25")

    snapshot = build_snapshot(FakePaperBroker(), state_dir, tmp_path / "status.json")

    assert snapshot["latest_cycle"]["status"] == "reconciled"
    assert snapshot["latest_cycle"]["confirmed_fills"] == 2
    assert snapshot["account"]["positions"] == {"QQQ": 8400.0, "SPY": 16700.0}
    serialized = json.dumps(snapshot)
    assert "paper-account" not in serialized
    assert "ALPACA" not in serialized


def test_completed_trial_weeks_include_successful_no_trade_cycles():
    records = [
        {
            "status": "execution_pending",
            "signal": {"as_of": "2026-09-30"},
            "orders": [{"order_id": "one"}],
        },
        {
            "status": "executed",
            "signal": {"as_of": "2026-10-07"},
            "orders": [],
        },
        {
            "status": "planned",
            "plan_only": True,
            "signal": {"as_of": "2026-10-14"},
            "orders": [],
        },
        {
            "status": "skipped_market_closed",
            "signal": {"as_of": "2026-10-21"},
            "orders": [],
        },
    ]

    assert _completed_trial_weeks(records) == 2


def test_completed_trial_weeks_deduplicate_same_signal_week():
    records = [
        {"status": "executed", "signal": {"as_of": "2026-10-07"}, "orders": []},
        {"status": "executed", "signal": {"as_of": "2026-10-08"}, "orders": []},
    ]

    assert _completed_trial_weeks(records) == 1
