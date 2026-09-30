"""Pure-Python deterministic blend used as the paper-account controller."""
from __future__ import annotations

import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from .. import studio_core as core
from .base import Signal, SignalProvider

MAX_DATA_AGE_DAYS = 5


def _tiingo_prices(ticker: str, start: str, end: str, token: str) -> dict[str, float]:
    query = urllib.parse.urlencode({"startDate": start, "endDate": end, "format": "json",
                                    "resampleFreq": "daily"})
    request = urllib.request.Request(
        f"https://api.tiingo.com/tiingo/daily/{ticker}/prices?{query}",
        headers={"Authorization": f"Token {token}", "User-Agent": "tsetlin-trader/1"}, method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        rows = json.loads(response.read().decode("utf-8"))
    values = {row["date"][:10]: float(row["adjClose"])
              for row in rows if row.get("adjClose") is not None}
    if not values:
        raise RuntimeError(f"Tiingo returned no adjusted prices for {ticker}")
    return values


class BlendSignalProvider(SignalProvider):
    """Equal blend of trend, momentum and defensive sleeves, without native libraries."""
    def __init__(self, prices_csv: str | Path = "data/tiingo-prices.csv",
                 tiingo_token: str | None = None, history_start: str = "2008-01-01",
                 max_data_age_days: int = MAX_DATA_AGE_DAYS) -> None:
        self.prices_csv = Path(prices_csv)
        self.tiingo_token = tiingo_token
        self.history_start = history_start
        self.max_data_age_days = max_data_age_days

    def _refresh(self) -> None:
        end = date.today().isoformat()
        series = {asset: _tiingo_prices(asset, self.history_start, end, self.tiingo_token)
                  for asset in core.ASSETS}
        common = sorted(set.intersection(*(set(values) for values in series.values())))
        if len(common) < 700:
            raise RuntimeError(f"Only {len(common)} common trading days; refusing partial Tiingo data")
        self.prices_csv.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.prices_csv.with_suffix(self.prices_csv.suffix + ".tmp")
        with open(temporary, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", *core.ASSETS])
            for day in common:
                writer.writerow([day, *[repr(series[asset][day]) for asset in core.ASSETS]])
        core.load_prices_csv(temporary)
        os.replace(temporary, self.prices_csv)

    def _prices(self) -> tuple[list[str], dict[str, list[float]]]:
        if self.tiingo_token:
            self._refresh()
        dates, prices = core.load_prices_csv(self.prices_csv)
        age = (date.today() - date.fromisoformat(dates[-1])).days
        if age > self.max_data_age_days:
            raise RuntimeError(f"Price data is {age} days old; refusing a stale blend signal")
        return dates, prices

    def get_current_signal(self) -> Signal:
        dates, prices = self._prices()
        targets = core.target_weights(prices)
        index = len(dates) - 1
        weights = {asset: weight for asset, weight in targets["blend"][index].items() if weight > 0}
        ma20 = core.sma(prices["SPY"], 20)[index]
        ma100 = core.sma(prices["SPY"], 100)[index]
        trend = targets["trend"][index]
        momentum = targets["momentum"][index]
        defensive = targets["defensive"][index]
        leader = next((asset for asset, weight in momentum.items() if weight), "cash")
        notes = [
            f"trend sleeve (1/3): SPY 20-day average {ma20:.2f} vs 100-day {ma100:.2f}; "
            f"target={'SPY' if trend['SPY'] else 'cash'}",
            f"momentum sleeve (1/3): strongest 60-day return target={leader}",
            f"defensive sleeve (1/3): SPY close {prices['SPY'][index]:.2f} vs 100-day {ma100:.2f}; "
            f"target={'SPY' if defensive['SPY'] else 'TLT'}",
            f"combined blend target before account risk scaling: {weights or {'cash': 1.0}}",
        ]
        return Signal(as_of=dates[-1], strategy="blend", target_weights=weights,
                      confidence=None, rule_trace=notes)
