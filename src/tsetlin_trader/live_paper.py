"""Persistent, local-first intraday runner for a dedicated Alpaca PAPER account.

Observe mode is the default. Paper mode requires separate intraday credentials.
The HTTP server binds to loopback and serves a read-only status API plus
operator-token protected pause, resume and flatten controls.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .broker.alpaca_client import AlpacaClient
from .intraday_trial import FEATURES, download_bars, morning_features, sessions
from .signal.tm_model import TsetlinEnsemble

NY = ZoneInfo("America/New_York")
STATE = Path("data/intraday-live-state.json")
PAGE = Path(__file__).resolve().parents[2] / "docs" / "live-bot.html"
FILLED = {"filled", "orderstatus.filled"}
FAILED = {"rejected", "canceled", "cancelled", "expired", "orderstatus.rejected",
          "orderstatus.canceled", "orderstatus.expired"}


def now_et() -> datetime:
    return datetime.now(timezone.utc).astimezone(NY)


def clock_label(now: datetime) -> str:
    return now.astimezone(NY).strftime("%H:%M")


def decide(bars: pd.DataFrame, day: str) -> dict:
    """Train on completed prior sessions; decide using today's morning only."""
    history = sessions(bars)
    history = history[history.day < day]
    if len(history) < 30 or history.label.nunique() < 2:
        raise ValueError(f"need 30 prior complete sessions with both labels; found {len(history)}")
    row = morning_features(bars, day)
    x = pd.DataFrame([{key: row[key] for key in FEATURES}])
    model = TsetlinEnsemble(clauses=60, threshold=20, epochs=5, seeds=(1, 2))
    model.fit(history[list(FEATURES)], history.label)
    votes = model.class_sums(x)
    winner = int(model.classes_[int(np.argmax(votes))])
    pro, con = model.explain(x, winner, top_n=3)
    def explain_clause(item):
        return {"vote": round(item.vote, 2), "literals": list(item.literals)}
    return {"day": day, "action": "buy_SPY" if winner == 1 else "cash",
            "as_of": "10:30 ET", "data_feed": "IEX", "training_sessions": len(history),
            "features": {key: row[key] for key in FEATURES},
            "votes": {str(k): round(float(v), 2) for k, v in zip(model.classes_, votes)},
            "for": [explain_clause(c) for c in pro],
            "against": [explain_clause(c) for c in con]}


class Runner:
    def __init__(self, mode: str = "observe", state_path: Path = STATE,
                 broker: AlpacaClient | None = None, data_client=download_bars,
                 token: str | None = None):
        if mode not in {"observe", "paper"}:
            raise ValueError("mode must be observe or paper")
        self.mode = mode
        self.path = state_path
        self.broker = broker
        self.data_client = data_client
        self.token = token or os.environ.get("INTRADAY_OPERATOR_TOKEN", "")
        if mode == "paper" and (broker is None or not self.token):
            raise ValueError("paper mode needs a paper broker and INTRADAY_OPERATOR_TOKEN")
        self.lock = threading.RLock()
        self.state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {
            "mode": mode, "paused": True, "day": None, "events": [], "decision": None,
            "entry_order": None, "exit_order": None, "managed_position": False}
        if self.state.get("mode") not in {None, mode}:
            raise ValueError("state belongs to another mode; use a separate state path")
        self.state["mode"] = mode
        self.state.setdefault("events", [])
        self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")
        temp.replace(self.path)

    def _event(self, kind: str, message: str, now: datetime):
        self.state["events"].append({"at": now.isoformat(), "kind": kind, "message": message})
        self.state["events"] = self.state["events"][-100:]

    def snapshot(self) -> dict:
        with self.lock:
            return {key: value for key, value in self.state.items()
                    if key not in {"start_equity"}}

    def control(self, action: str, token: str, now: datetime | None = None) -> dict:
        if not self.token or not hmac.compare_digest(token, self.token):
            raise PermissionError("invalid operator token")
        now = now or now_et()
        with self.lock:
            if action == "pause":
                self.state["paused"] = True
                self._event("operator", "New entries paused", now)
            elif action == "resume":
                self.state["paused"] = False
                self._event("operator", "New entries enabled", now)
            elif action == "flatten":
                self.state["paused"] = True
                self._flatten(now)
            else:
                raise ValueError("unknown action")
            self._save()
            return self.snapshot()

    def _flatten(self, now: datetime):
        if self.mode != "paper" or self.broker is None:
            self._event("operator", "Observe mode has no broker position", now)
            return
        entry = self.state.get("entry_order")
        if entry and entry.get("order_id") and str(entry.get("status", "")).lower() not in FILLED | FAILED:
            try:
                self.broker.cancel_order(entry["order_id"])
                self._event("order", "Cancel requested for outstanding SPY entry", now)
            except Exception as exc:
                self._event("blocked", f"Could not cancel entry: {type(exc).__name__}", now)
        positions = self.broker.get_positions()
        if not self.state.get("managed_position") or positions.get("SPY", 0) <= 0:
            self._event("operator", "No bot-managed SPY position to close", now)
            return
        if self.broker.open_order_symbols():
            self._event("blocked", "Open broker order; close deferred", now)
            return
        self.state["exit_intent"] = True
        self._save()
        order = self.broker.close_position("SPY")
        self.state["exit_order"] = order.__dict__
        self._event("order", "SPY close submitted; fill unconfirmed", now)

    def _reconcile(self, now: datetime):
        if self.mode != "paper":
            return
        for key in ("entry_order", "exit_order"):
            order = self.state.get(key)
            if order and order.get("order_id"):
                order["status"] = self.broker.get_order_status(order["order_id"])
        positions = self.broker.get_positions()
        if self.state.get("entry_order") and positions.get("SPY", 0) > 0:
            self.state["managed_position"] = True
        if self.state.get("managed_position") and positions.get("SPY", 0) <= 0:
            self.state["managed_position"] = False
            if self.state.get("exit_order"):
                self._event("fill", "SPY position is flat at broker", now)
        self.state["positions"] = {s: round(v, 2) for s, v in positions.items()}

    def tick(self, now: datetime | None = None):
        now = now or now_et()
        day = now.astimezone(NY).date().isoformat()
        with self.lock:
            try:
                self._tick(now, day)
                self.state["last_error"] = None
            except Exception as exc:
                self.state["last_error"] = f"{type(exc).__name__}: {exc}"
                self._event("error", self.state["last_error"], now)
            self.state["updated_at"] = now.isoformat()
            self._save()

    def _tick(self, now: datetime, day: str):
        prior_day_position = False
        if self.state.get("day") != day:
            if self.mode == "paper" and self.state.get("managed_position"):
                prior_day_position = True
                self.state["paused"] = True
                self._event("blocked", "Prior-day position remains; entry disabled", now)
            self.state.update(day=day, decision=None, entry_order=None,
                              exit_order=None, entry_intent=False, exit_intent=False,
                              start_equity=None, loss_limit_hit=False)
        self._reconcile(now)
        clock = clock_label(now)
        if self.mode == "paper":
            if prior_day_position and "09:30" <= clock <= "15:58":
                self._flatten(now)
            account = self.broker.get_account()
            if self.state.get("start_equity") is None:
                self.state["start_equity"] = account.equity
            start = self.state["start_equity"]
            if start and account.equity < start * .99:
                self.state["paused"] = True
                if not self.state.get("loss_limit_hit"):
                    self.state["loss_limit_hit"] = True
                    self._event("risk", "Daily account loss limit reached", now)
                if "09:30" <= clock <= "15:58" and self.state.get("managed_position") and not self.state.get("exit_order"):
                    self._flatten(now)
            self.state["equity_change_pct"] = round((account.equity / start - 1) * 100, 3) if start else None
            exit_order = self.state.get("exit_order")
            retry_exit = not exit_order or str(exit_order.get("status", "")).lower() in FAILED
            entry_order = self.state.get("entry_order")
            entry_pending = entry_order and str(entry_order.get("status", "")).lower() not in FILLED | FAILED
            if "15:45" <= clock <= "15:58" and (self.state.get("managed_position") or
                 entry_pending) and retry_exit:
                self._flatten(now)
        if not ("10:30" <= clock <= "10:40") or self.state.get("decision"):
            return
        if now.astimezone(NY).weekday() >= 5:
            return
        if self.mode == "paper" and self.state.get("paused"):
            return
        if self.mode == "paper" and not self.broker.is_full_trading_day(now.astimezone(NY).date()):
            return
        prefix = "INTRADAY_" if self.mode == "paper" else ""
        key = os.environ.get(f"{prefix}ALPACA_API_KEY", "")
        secret = os.environ.get(f"{prefix}ALPACA_SECRET_KEY", "")
        if not key or not secret:
            raise ValueError("paper market-data credentials are missing")
        bars = self.data_client((now.date() - timedelta(days=90)).isoformat(),
                                now.astimezone(timezone.utc).isoformat(),
                                key, secret)
        decision = decide(bars, day)
        self.state["decision"] = decision
        self._event("signal", f"TM chose {decision['action']} from completed 10:25 ET bar", now)
        self._save()
        if self.mode != "paper" or decision["action"] != "buy_SPY":
            return
        if self.state.get("entry_intent") or self.state.get("managed_position"):
            return
        if not self.broker.is_market_open():
            self._event("blocked", "Broker market clock says closed", now)
            return
        positions = self.broker.get_positions()
        if positions:
            self.state["paused"] = True
            self._event("blocked", "Dedicated account has an existing position; entry disabled", now)
            return
        if self.broker.open_order_symbols():
            self._event("blocked", "An open broker order exists; entry disabled", now)
            return
        account = self.broker.get_account()
        notional = min(5000.0, account.equity * .05, account.cash * .95)
        if notional < 1:
            self._event("blocked", "Insufficient paper cash", now)
            return
        self.state["entry_intent"] = True
        self._save()  # crash after this point never repeats the entry
        order = self.broker.submit_order("SPY", notional, "buy", f"tt-intraday-{day}-SPY-buy")
        self.state["entry_order"] = order.__dict__
        self._event("order", "SPY buy submitted; fill unconfirmed", now)


def serve(runner: Runner, host: str = "127.0.0.1", port: int = 8765):
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("dashboard must bind to loopback; use a private tunnel for remote access")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/status":
                body = json.dumps(runner.snapshot()).encode()
                content_type = "application/json"
            elif self.path in {"/", "/index.html"}:
                body = PAGE.read_bytes()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path not in {"/api/pause", "/api/resume", "/api/flatten"}:
                self.send_error(404)
                return
            try:
                result = runner.control(self.path.rsplit("/", 1)[-1],
                                        self.headers.get("X-Operator-Token", ""))
            except PermissionError:
                self.send_error(403)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer((host, port), Handler)
    def loop():
        while True:
            runner.tick()
            time.sleep(15)
    threading.Thread(target=loop, daemon=True).start()
    print(f"Paper bot dashboard: http://{host}:{port}")
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["observe", "paper"], default="observe")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    broker = None
    if args.mode == "paper":
        key = os.environ["INTRADAY_ALPACA_API_KEY"]
        secret = os.environ["INTRADAY_ALPACA_SECRET_KEY"]
        broker = AlpacaClient(key, secret)  # hardcoded paper=True in adapter
    state_path = STATE if args.mode == "paper" else Path("data/intraday-observe-state.json")
    serve(Runner(args.mode, state_path=state_path, broker=broker), port=args.port)


if __name__ == "__main__":
    main()

