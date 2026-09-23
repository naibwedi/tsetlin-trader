"""Broker-free controller and shadow-model preview for CI and operators."""

from __future__ import annotations

import json

from .run_cycle import build_signal_provider, load_dotenv, shadow_signals


def preview() -> dict:
    """Calculate signals without reading an account or creating an order."""
    controller = build_signal_provider("blend").get_current_signal()
    return {
        "mode": "NO ORDERS — broker is not contacted",
        "controller": controller.model_dump(mode="json"),
        "shadows": shadow_signals("blend"),
    }


def main() -> None:
    load_dotenv()
    print(json.dumps(preview(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
