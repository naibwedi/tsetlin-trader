"""Orchestrates one trading cycle: signal -> risk -> rebalance -> log.

    python -m tsetlin_trader.run_cycle                       # uses .env / defaults
    python -m tsetlin_trader.run_cycle --plan-only           # show trades, place none
    python -m tsetlin_trader.run_cycle --broker simulated --signal mock

Environment (see .env.example): BROKER_PROVIDER, SIGNAL_PROVIDER, SIGNAL_MODEL,
TIINGO_API_TOKEN, ALPACA_API_KEY, ALPACA_SECRET_KEY, MAX_DRAWDOWN_PCT,
POSITION_FRACTION.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .broker.alpaca_client import AlpacaClient
from .broker.base import BrokerClient
from .broker.rebalance import execute, plan_rebalance
from .broker.simulated_client import SimulatedBroker
from .logging.decision_log import DecisionLog
from .risk.manager import Decision, RiskManager
from .signal.base import SignalProvider
from .signal.mock_provider import MockSignalProvider

STATE_PATH = Path("results/state.json")


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
            model=os.environ.get("SIGNAL_MODEL", "bernoulli"),
            tiingo_token=os.environ.get("TIINGO_API_TOKEN"),
        )
    raise ValueError(f"Unknown SIGNAL_PROVIDER: {provider!r} (expected 'logic_alpha' or 'mock')")


def run(
    broker_provider: str | None = None,
    signal_provider: str | None = None,
    plan_only: bool = False,
) -> dict:
    broker_name = broker_provider or os.environ.get("BROKER_PROVIDER", "simulated")
    signal_name = signal_provider or os.environ.get("SIGNAL_PROVIDER", "logic_alpha")

    broker = build_broker(broker_name)
    log = DecisionLog()
    entry: dict = {"broker_provider": broker_name, "signal_provider": signal_name, "plan_only": plan_only}

    pending = broker.open_order_symbols()
    if pending:
        entry.update(status="skipped_open_orders_pending", pending_symbols=sorted(pending))
        log.append(entry)
        return entry

    signal = build_signal_provider(signal_name).get_current_signal()
    account = broker.get_account()
    positions = broker.get_positions()

    risk = RiskManager.from_state_file(
        STATE_PATH,
        max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", 0.15)),
        position_fraction=float(os.environ.get("POSITION_FRACTION", 0.25)),
    )
    decision = risk.size_order(signal.target_weights, account.equity)
    if not plan_only:
        risk.save_state(STATE_PATH)

    target_values = {s: account.equity * w for s, w in decision.sized_weights.items()}
    trades = plan_rebalance(positions, target_values, account.equity)
    orders = [] if plan_only else [o.__dict__ for o in execute(broker, trades)]

    entry.update(
        status="planned" if plan_only else "executed",
        signal=signal.model_dump(mode="json"),
        risk_decision=decision.decision.value,
        risk_reason=decision.reason,
        equity=account.equity,
        positions_before=positions,
        target_values=target_values,
        trades=[t.__dict__ for t in trades],
        orders=orders,
    )
    log.append(entry)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--broker", choices=["simulated", "alpaca"], default=None)
    parser.add_argument("--signal", choices=["logic_alpha", "mock"], default=None)
    parser.add_argument("--plan-only", action="store_true", help="Compute and print trades without placing them")
    args = parser.parse_args()
    result = run(broker_provider=args.broker, signal_provider=args.signal, plan_only=args.plan_only)

    print(f"status:   {result['status']}")
    if "signal" in result:
        s = result["signal"]
        print(f"signal:   {s['strategy']} (confidence {s['confidence']:.2f}, as of {s['as_of']})")
        for line in s["rule_trace"]:
            print(f"          - {line}")
        print(f"risk:     {result['risk_decision']} - {result['risk_reason']}")
        print(f"equity:   ${result['equity']:,.2f}   positions: {result['positions_before']}")
        for t in result["trades"]:
            print(f"trade:    {t['side']} {t['symbol']} ${t['notional']:,.2f}{' (close all)' if t['close_all'] else ''}")
        if not result["trades"]:
            print("trade:    none (already at target)")
    else:
        print(f"pending:  {result.get('pending_symbols')}")


if __name__ == "__main__":
    main()
