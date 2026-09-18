"""Orchestrates one trading cycle: signal -> risk check -> order -> log.

Run weekly via .github/workflows/weekly-trade.yml, or manually:

    python -m tsetlin_trader.run_cycle                  # BROKER_PROVIDER=simulated (default), no API keys needed
    BROKER_PROVIDER=alpaca python -m tsetlin_trader.run_cycle   # real Alpaca paper account
"""

from __future__ import annotations

import argparse
import os

from .broker.alpaca_client import AlpacaClient
from .broker.base import BrokerClient
from .broker.simulated_client import SimulatedBroker
from .logging.decision_log import DecisionLog
from .risk.manager import Decision, RiskManager
from .signal.mock_provider import MockSignalProvider


def build_broker(provider: str) -> BrokerClient:
    if provider == "simulated":
        return SimulatedBroker()
    if provider == "alpaca":
        return AlpacaClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
    raise ValueError(f"Unknown BROKER_PROVIDER: {provider!r} (expected 'simulated' or 'alpaca')")


def run(broker_provider: str | None = None) -> dict:
    signal_provider = MockSignalProvider()
    risk_manager = RiskManager(
        max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", 0.15)),
        position_fraction=float(os.environ.get("POSITION_FRACTION", 0.25)),
    )
    log = DecisionLog()
    broker = build_broker(broker_provider or os.environ.get("BROKER_PROVIDER", "simulated"))

    signal = signal_provider.get_current_signal()
    equity = broker.get_account().equity
    risk_decision = risk_manager.size_order(signal.target_weights, equity)

    orders = []
    if risk_decision.decision == Decision.TRADE:
        for symbol, weight in risk_decision.sized_weights.items():
            orders.append(broker.submit_order(symbol, weight, equity).__dict__)

    entry = {
        "signal": signal.model_dump(mode="json"),
        "risk_decision": risk_decision.decision.value,
        "risk_reason": risk_decision.reason,
        "sized_weights": risk_decision.sized_weights,
        "orders": orders,
        "equity": equity,
        "broker_provider": broker_provider or os.environ.get("BROKER_PROVIDER", "simulated"),
    }
    log.append(entry)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--broker",
        choices=["simulated", "alpaca"],
        default=None,
        help="Overrides BROKER_PROVIDER env var. Defaults to 'simulated' (no API keys required).",
    )
    args = parser.parse_args()
    result = run(broker_provider=args.broker)
    print(result)


if __name__ == "__main__":
    main()
