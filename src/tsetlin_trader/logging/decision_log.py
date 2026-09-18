"""Appends one JSON line per trading cycle to results/decisions.jsonl.

Every entry carries the signal's rule trace next to the resulting order(s),
so backtest-vs-live behavior and model rationale stay auditable over time.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_LOG_PATH = Path("results/decisions.jsonl")


class DecisionLog:
    def __init__(self, path: Path = DEFAULT_LOG_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, entry: dict[str, Any]) -> None:
        record = {"logged_at": datetime.now(timezone.utc).isoformat(), **entry}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
