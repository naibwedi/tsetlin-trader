"""Orchestrates one trading cycle.

Order of operations, chosen so that safety never depends on the model:

    lock -> pending orders? -> account + risk state -> drawdown breaker
         -> (HALT: liquidate, no model needed) -> signal -> rebalance -> log

    python -m tsetlin_trader.run_cycle                       # uses .env / defaults
    python -m tsetlin_trader.run_cycle --plan-only           # show trades, place none
    python -m tsetlin_trader.run_cycle --broker simulated --signal mock

Environment (see .env.example): BROKER_PROVIDER, SIGNAL_PROVIDER, SIGNAL_MODEL,
SIGNAL_HISTORY_START, SHADOW_MODELS, TIINGO_API_TOKEN, ALPACA_API_KEY,
ALPACA_SECRET_KEY, MAX_DRAWDOWN_PCT, POSITION_FRACTION, ALLOW_FRESH_STATE,
ALERT_WEBHOOK_URL.
"""

from __future__ import annotations

import argparse
import os
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path
import json

from .broker.alpaca_client import AlpacaClient
from .broker.base import BrokerClient
from .broker.rebalance import execute, plan_rebalance
from .broker.simulated_client import SimulatedBroker
from .logging.decision_log import DecisionLog
from .ops import alert, fingerprint
from .risk.manager import Decision, RiskManager
from .signal.base import UNIVERSE, SignalProvider
from .signal.mock_provider import MockSignalProvider
from .trial_report import publish as publish_trial_report
from .storage import AlreadyRunning, process_lock, atomic_json

STATE_PATH = Path("results/state.json")
LOCK_PATH = Path("results/.run.lock")
LOCK_STALE_S = 2 * 60 * 60
PRICES_PATH = Path("data/tiingo-prices.csv")


def load_dotenv(path: Path = Path(".env")) -> None:
    """Read KEY=VALUE lines into os.environ without overriding existing variables."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


@contextmanager
def run_lock(path: Path = LOCK_PATH, stale_s: float = LOCK_STALE_S):
    """Kernel lock releases on crash; file age never overrides a live owner."""
    with process_lock(path):
        yield


def build_broker(provider: str) -> BrokerClient:
    if provider == "simulated":
        return SimulatedBroker()
    if provider == "alpaca":
        return AlpacaClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
    raise ValueError(f"Unknown BROKER_PROVIDER: {provider!r} (expected 'simulated' or 'alpaca')")


def build_signal_provider(provider: str) -> SignalProvider:
    if provider == "mock":
        return MockSignalProvider()
    if provider == "logic_alpha":
        from .signal.logic_alpha_provider import LogicAlphaProvider

        return LogicAlphaProvider(
            prices_csv=PRICES_PATH,
            model=os.environ.get("SIGNAL_MODEL", "tmu"),
            tiingo_token=os.environ.get("TIINGO_API_TOKEN"),
            history_start=os.environ.get("SIGNAL_HISTORY_START", "2008-01-01"),
        )
    if provider == "blend":
        from .signal.blend_provider import BlendSignalProvider
        return BlendSignalProvider(
            prices_csv=PRICES_PATH,
            tiingo_token=os.environ.get("TIINGO_API_TOKEN"),
            history_start=os.environ.get("SIGNAL_HISTORY_START", "2008-01-01"),
        )
    raise ValueError(f"Unknown SIGNAL_PROVIDER: {provider!r} (expected 'blend', 'logic_alpha' or 'mock')")


def shadow_signals(main_model: str) -> dict:
    """What the other models would have said today. Logged for later comparison, never traded."""
    from .signal.logic_alpha_provider import LogicAlphaProvider

    wanted = [m.strip() for m in os.environ.get("SHADOW_MODELS", "bernoulli").split(",") if m.strip()]
    results = {}
    for model in wanted:
        if model == main_model:
            continue
        try:
            signal = LogicAlphaProvider(
                prices_csv=PRICES_PATH, model=model,
                history_start=os.environ.get("SIGNAL_HISTORY_START", "2008-01-01"),
            ).get_current_signal()
            results[model] = {
                "strategy": signal.strategy, "target_weights": signal.target_weights,
                "confidence": signal.confidence, "as_of": signal.as_of,
            }
        except Exception as exc:  # a shadow failure must never block the real trade
            results[model] = {"error": f"{type(exc).__name__}: {exc}"}
    return results


def virtual_portfolios(tm_weights: dict[str, float], as_of: str) -> dict:
    """Blend vs blend+filter vs TM, marked on the same prices. Never blocks the real trade."""
    try:
        from logic_alpha_tm.data import load_prices_csv

        from . import portfolios

        prices = load_prices_csv(PRICES_PATH)
        return portfolios.step(prices, portfolios.build_targets(prices, tm_weights), as_of,
                               state_path=Path(os.environ.get("TT_STATE_DIR", "results")) / "portfolios.json")
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _run(broker_name: str, signal_name: str, plan_only: bool, log: DecisionLog) -> dict:
    broker = build_broker(broker_name)
    entry: dict = {"broker_provider": broker_name, "signal_provider": signal_name, "plan_only": plan_only}
    state_path = Path(os.environ.get("TT_STATE_DIR", "results")) / "state.json"
    if broker_name == "alpaca" and not os.environ.get("TT_STATE_DIR"):
        entry.update(status="refused_missing_state", reason="Set TT_STATE_DIR to a private durable directory")
        return entry

    entry["fingerprint"] = fingerprint(PRICES_PATH)

    if not plan_only and not broker.is_trading_day(date.today()):
        entry.update(status="skipped_market_closed", reason=f"{date.today()} is not a trading day")
        return entry

    # Safety first: account state and the drawdown breaker never wait on data or a model.
    if broker_name == "alpaca" and not state_path.exists() and os.environ.get("ALLOW_FRESH_STATE") != "1":
        entry.update(status="refused_missing_state",
                     reason=f"{STATE_PATH} is missing; set ALLOW_FRESH_STATE=1 once to start a new history")
        alert(f"tsetlin-trader refused to run: {entry['reason']}")
        return entry

    account = broker.get_account()
    if broker_name == "alpaca":
        identity_path = state_path.with_name("account.json")
        identity = broker.account_identity()
        if identity_path.exists():
            if json.loads(identity_path.read_text())["account_id"] != identity:
                raise ValueError("private state belongs to another paper account")
        elif state_path.exists() or os.environ.get("ALLOW_FRESH_STATE") != "1":
            raise ValueError("unbound/legacy state: manual account reconciliation required")
        elif not plan_only:
            atomic_json(identity_path, {"account_id": identity})
    positions = broker.get_positions()
    risk = RiskManager.from_state_file(
        state_path,
        max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", 0.15)),
        position_fraction=float(os.environ.get("POSITION_FRACTION", 0.25)),
    )
    entry.update(equity=account.equity, positions_before=positions)

    def finish(target_values: dict[str, float], decision, cycle_id: str) -> dict:
        plan = plan_rebalance(positions, target_values, account.equity, UNIVERSE)
        if not plan_only:
            risk.save_state(state_path)
        orders = [] if plan_only else [o.__dict__ for o in execute(broker, plan.trades, cycle_id)]
        failed_sells = [o for o in orders if o["side"] == "sell" and
                        str(o["status"]).lower() not in ("filled", "orderstatus.filled")]
        incomplete = bool(failed_sells or any(
            str(o["status"]).lower() in ("rejected", "orderstatus.rejected", "canceled", "orderstatus.canceled")
            or str(o["status"]).startswith("skipped_") for o in orders))
        unconfirmed = any(str(o["status"]).lower() not in ("filled", "orderstatus.filled") for o in orders)
        if incomplete:
            alert("tsetlin-trader: one or more sells were not confirmed filled; check the paper account.")
        elif decision.decision == Decision.HALT and not plan_only:
            alert(f"tsetlin-trader HALT: {decision.reason}. Liquidated: {[t.symbol for t in plan.trades]}")
        entry.update(
            status="planned" if plan_only else "execution_incomplete" if incomplete else "execution_pending" if unconfirmed else "executed",
            risk_decision=decision.decision.value, risk_reason=decision.reason,
            target_values=target_values, ignored_symbols=plan.ignored_symbols,
            trades=[t.__dict__ for t in plan.trades], orders=orders,
        )
        return entry

    halt = risk.size_order({}, account.equity)
    if not plan_only:
        risk.save_state(state_path)
    pending = broker.open_order_symbols()
    if pending:
        # Do not submit conflicting liquidation orders, but always persist
        # a tripped breaker and alert the operator before returning.
        entry.update(status="skipped_open_orders_pending", pending_symbols=sorted(pending),
                     risk_decision=halt.decision.value, risk_reason=halt.reason)
        if halt.decision == Decision.HALT:
            alert("HALT: pending orders block liquidation; cancel/reconcile at the paper broker")
        return entry
    if halt.decision == Decision.HALT:
        entry["signal"] = None
        return finish({}, halt, f"{date.today():%Y%m%d}-halt")

    signal = build_signal_provider(signal_name).get_current_signal()
    entry["signal"] = signal.model_dump(mode="json")
    if signal_name in {"logic_alpha", "blend"}:
        main_model = os.environ.get("SIGNAL_MODEL", "tmu") if signal_name == "logic_alpha" else "blend"
        entry["shadow_signals"] = shadow_signals(main_model)
        tm_weights = signal.target_weights
        if signal_name == "blend":
            tm_shadow = entry["shadow_signals"].get("tmu", {})
            if "error" in tm_shadow:
                entry["virtual_portfolios"] = {
                    "error": "TM shadow unavailable; virtual portfolios were not advanced"
                }
                tm_weights = None
            else:
                tm_weights = tm_shadow.get("target_weights", {})
        if not plan_only and signal.as_of and tm_weights is not None:
            entry["virtual_portfolios"] = virtual_portfolios(tm_weights, signal.as_of)

    decision = risk.size_order(signal.target_weights, account.equity)
    target_values = {s: account.equity * w for s, w in decision.sized_weights.items()}
    return finish(target_values, decision, f"{signal.as_of or date.today().isoformat()}")


def run(
    broker_provider: str | None = None,
    signal_provider: str | None = None,
    plan_only: bool = False,
) -> dict:
    broker_name = broker_provider or os.environ.get("BROKER_PROVIDER", "simulated")
    signal_name = signal_provider or os.environ.get("SIGNAL_PROVIDER", "logic_alpha")
    state_dir = Path(os.environ.get("TT_STATE_DIR", "results"))
    log = DecisionLog(state_dir / "decisions.jsonl")
    def refresh_page() -> None:
        if os.environ.get("TT_STATE_DIR"):
            return  # private runtime never automatically publishes account data
        try:
            publish_trial_report(log.path)
        except Exception as exc:
            alert(f"tsetlin-trader could not update the public trial page: {type(exc).__name__}: {exc}")

    try:
        with run_lock(state_dir / ".run.lock"):
            entry = _run(broker_name, signal_name, plan_only, log)
    except AlreadyRunning as exc:
        entry = {"broker_provider": broker_name, "signal_provider": signal_name,
                 "plan_only": plan_only, "status": "skipped_locked", "reason": str(exc)}
    except Exception as exc:
        log.append({"broker_provider": broker_name, "signal_provider": signal_name, "plan_only": plan_only,
                    "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        refresh_page()
        alert(f"tsetlin-trader FAILED: {type(exc).__name__}: {exc}")
        raise
    log.append(entry)
    refresh_page()
    return entry


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", choices=["simulated", "alpaca"], default=None)
    parser.add_argument("--signal", choices=["blend", "logic_alpha", "mock"], default=None)
    parser.add_argument("--plan-only", action="store_true", help="Compute and print trades without placing them")
    args = parser.parse_args()
    result = run(broker_provider=args.broker, signal_provider=args.signal, plan_only=args.plan_only)

    print(f"status:   {result['status']}")
    if result["status"] in ("skipped_open_orders_pending", "skipped_locked", "refused_missing_state", "skipped_market_closed"):
        print(f"reason:   {result.get('reason') or result.get('pending_symbols')}")
        return
    s = result.get("signal")
    if s:
        conf = "n/a" if s["confidence"] is None else f"{s['confidence']:.2f}"
        print(f"signal:   {s['strategy']} (confidence {conf}, as of {s['as_of']})")
        for line in s["rule_trace"]:
            print(f"          - {line}")
    for model, sh in result.get("shadow_signals", {}).items():
        print(f"shadow:   {model} would say {sh.get('strategy', 'ERROR ' + sh.get('error', ''))} (not traded)")
    print(f"risk:     {result['risk_decision']} - {result['risk_reason']}")
    print(f"equity:   ${result['equity']:,.2f}   positions: {result['positions_before']}")
    if result.get("ignored_symbols"):
        print(f"ignored:  {result['ignored_symbols']} (not in universe, left untouched)")
    for t in result["trades"]:
        print(f"trade:    {t['side']} {t['symbol']} ${t['notional']:,.2f}{' (close all)' if t['close_all'] else ''}")
    if not result["trades"]:
        print("trade:    none (already at target)")
    vp = result.get("virtual_portfolios")
    if vp and "portfolios" in vp:
        print(f"virtual:  since {vp['started']} (as of {vp['as_of']})")
        for name, p in vp["portfolios"].items():
            print(f"          {name:<15} {p['return_since_start']:+.2%}")
    elif vp:
        print(f"virtual:  {vp['error']}")


if __name__ == "__main__":
    main()

