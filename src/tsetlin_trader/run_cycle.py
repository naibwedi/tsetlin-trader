"""Orchestrates one trading cycle: signal -> risk check -> paper order -> log.

Run weekly via .github/workflows/weekly-trade.yml, or manually:

    python -m tsetlin_trader.run_cycle --dry-run
"""

from __future__ import annotations

import argparse
import os

from .broker.alpaca_client import AlpacaClient
from .logging.decision_log import DecisionLog
from .risk.manager import Decision, RiskManager
from .signal.mock_provider import MockSignalProvider


def run(dry_run: bool = False) -> dict:
    signal_provider = MockSignalProvider()
    risk_manager = RiskManager(
        max_drawdown_pct=float(os.environ.get("MAX_DRAWDOWN_PCT", 0.15)),
        position_fraction=float(os.environ.get("POSITION_FRACTION", 0.25)),
    )
    log = DecisionLog()

    signal = signal_provider.get_current_signal()

    if dry_run:
        equity = 100_000.0
        orders = []
    else:
        client = AlpacaClient(
            api_key=os.environ["ALPACA_API_KEY"],
            secret_key=os.environ["ALPACA_SECRET_KEY"],
        )
        equity = client.get_account().equity
        orders = []

    risk_decision = risk_manager.size_order(signal.target_weights, equity)

    if risk_decision.decision == Decision.TRADE and not dry_run:
        for symbol, weight in risk_decision.sized_weights.items():
            orders.append(client.submit_order(symbol, weight, equity).__dict__)

    entry = {
        "signal": signal.model_dump(mode="json"),
        "risk_decision": risk_decision.decision.value,
        "risk_reason": risk_decision.reason,
        "sized_weights": risk_decision.sized_weights,
        "orders": orders,
        "equity": equity,
        "dry_run": dry_run,
    }
    log.append(entry)
    return entry


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Run without hitting the Alpaca API")
    args = parser.parse_args()
    result = run(dry_run=args.dry_run)
    print(result)


if __name__ == "__main__":
    main()
