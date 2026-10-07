"""Publish a sanitized, read-only snapshot for the static public dashboard.

This module never submits, cancels, or replaces orders. The broker adapter is
permanently bound to Alpaca's paper endpoint. Account IDs and credentials are
used only for local verification and are never included in the output.
"""
from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path

from .broker.alpaca_client import AlpacaClient
from .run_cycle import load_dotenv
from .storage import atomic_json


FINAL_FILLED = {"filled", "orderstatus.filled"}
FINAL_REJECTED = {"rejected", "orderstatus.rejected", "canceled", "orderstatus.canceled"}


def _latest_decision(state_dir: Path) -> tuple[dict, list[dict]]:
    path = state_dir / "decisions.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        raise ValueError("decision log is empty")
    return records[-1], records


def _sleeves(signal: dict) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    for line in signal.get("rule_trace", []):
        lower = line.lower()
        for name in ("trend", "momentum", "defensive"):
            if f"{name} sleeve" in lower and "target=" in line:
                target = line.rsplit("target=", 1)[1].strip()
                found.append({"name": name.title(), "target": target})
    return found


def build_snapshot(broker: AlpacaClient, state_dir: Path, output_path: Path) -> dict:
    risk_state = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    binding = json.loads((state_dir / "account.json").read_text(encoding="utf-8"))
    if binding.get("account_id") != broker.account_identity():
        raise ValueError("paper account binding mismatch")

    latest, records = _latest_decision(state_dir)
    account = broker.get_account()
    positions = broker.get_positions()
    open_orders = sorted(broker.open_order_symbols())
    all_statuses: list[str] = []
    latest_statuses: list[str] = []
    for record in records:
        statuses = []
        for order in record.get("orders", []):
            status = broker.get_order_status(order["order_id"]) if order.get("order_id") else str(order.get("status", "unknown"))
            statuses.append(status.lower())
        all_statuses.extend(statuses)
        if record is latest:
            latest_statuses = statuses

    completed = [r for r in records if not r.get("plan_only") and r.get("orders")]
    peak = float(risk_state.get("peak_equity", account.equity))
    drawdown = max(0.0, (peak - account.equity) / peak * 100.0) if peak else 0.0
    prior: dict = {}
    if output_path.exists():
        prior = json.loads(output_path.read_text(encoding="utf-8"))
    history = list(prior.get("equity_history", []))
    point = {"at": date.today().isoformat(), "equity": round(account.equity, 2)}
    if history and history[-1].get("at") == point["at"]:
        history[-1] = point
    else:
        history.append(point)

    signal = latest.get("signal") or {}
    filled = sum(status in FINAL_FILLED for status in all_statuses)
    rejected = sum(status in FINAL_REJECTED for status in all_statuses)
    reconciled = not open_orders and all(status in FINAL_FILLED | FINAL_REJECTED for status in latest_statuses)
    starting_equity = float(prior.get("trial", {}).get("starting_equity", peak))
    incident_count = int(prior.get("trial", {}).get("safety_incidents", 0))
    if rejected and rejected > int(prior.get("latest_cycle", {}).get("rejected_orders", 0)):
        incident_count += 1

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "trial": {
            "protocol_weeks": 26,
            "completed_weeks": len(completed),
            "status": "halted" if risk_state.get("halted") else "collecting_evidence",
            "starting_equity": round(starting_equity, 2),
            "total_confirmed_fills": filled,
            "safety_incidents": incident_count,
        },
        "account": {
            "equity": round(account.equity, 2),
            "cash": round(account.cash, 2),
            "positions": {symbol: round(value, 2) for symbol, value in sorted(positions.items())},
            "open_orders": open_orders,
        },
        "risk": {
            "halted": bool(risk_state.get("halted")),
            "current_drawdown_pct": round(drawdown, 4),
            "max_drawdown_pct": float(os.environ.get("MAX_DRAWDOWN_PCT", "0.15")) * 100,
            "position_fraction_pct": float(os.environ.get("POSITION_FRACTION", "0.25")) * 100,
        },
        "latest_cycle": {
            "signal": signal.get("strategy", latest.get("signal_provider", "unknown")),
            "signal_as_of": signal.get("as_of"),
            "status": "reconciled" if reconciled else "requires_attention",
            "confirmed_fills": sum(status in FINAL_FILLED for status in latest_statuses),
            "rejected_orders": sum(status in FINAL_REJECTED for status in latest_statuses),
            "sleeves": _sleeves(signal),
        },
        "safety": {
            "paper_only": True,
            "account_binding": True,
            "risk_integrity": isinstance(risk_state.get("peak_equity"), (int, float)) and isinstance(risk_state.get("halted"), bool),
            "orders_reconciled": reconciled,
            "data_fresh": bool(signal.get("as_of")),
        },
        "equity_history": history,
        "alert": None if reconciled and not risk_state.get("halted") else "Manual review required before another paper cycle.",
    }


def main() -> None:
    load_dotenv()
    state_dir = Path(os.environ["TT_STATE_DIR"])
    output = Path(os.environ.get("DASHBOARD_STATUS_PATH", "dashboard/data/status.json"))
    broker = AlpacaClient(os.environ["ALPACA_API_KEY"], os.environ["ALPACA_SECRET_KEY"])
    snapshot = build_snapshot(broker, state_dir, output)
    atomic_json(output, snapshot)
    print(f"Published sanitized dashboard snapshot: {output}")


if __name__ == "__main__":
    main()
