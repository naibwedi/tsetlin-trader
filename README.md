# tsetlin-trader

**An interpretable ML trading engine that paper-trades on live markets using Tsetlin Machine rule-based signals.**

Most "AI trading bot" repos are black boxes: a model spits out buy/sell and nobody — including the author — can say why. `tsetlin-trader` is built the other way around. Every trading decision carries the exact logical clauses (human-readable `IF ... AND ... THEN ...` rules) that fired to produce it, logged alongside the trade itself. It executes against [Alpaca's paper trading API](https://alpaca.markets/) — real market data, real order simulation, zero real money.

This repo is the **production/execution layer**. The signal-generation research — leakage-aware backtesting, walk-forward validation, Boolean feature engineering — lives in the companion research repo, [`logic-alpha-tm`](https://github.com/naibwedi/logic-alpha-tm). This repo wraps that research behind a clean interface and puts it to work on a schedule.

> **Status**: paper trading only. No real capital, no investment advice, no brokerage service. Educational / portfolio project.

## Why this exists

- **Interpretability as a first-class output.** Every decision in `results/decisions.jsonl` includes the rule trace that produced it — not just the trade.
- **A clean seam between research and execution.** The `SignalProvider` interface (`src/tsetlin_trader/signal/base.py`) is the only thing execution code depends on. Swap the model without touching risk, broker, or logging code.
- **Risk management isn't an afterthought.** Fixed-fraction position sizing plus a max-drawdown circuit breaker that halts trading automatically — see `src/tsetlin_trader/risk/manager.py`.

## Architecture

```
                 ┌─────────────────────┐
                 │   SignalProvider     │   (today: MockSignalProvider
                 │  get_current_signal()│    tomorrow: logic-alpha-tm model)
                 └──────────┬───────────┘
                            │ Signal{strategy, confidence, rule_trace}
                            ▼
                 ┌─────────────────────┐
                 │    RiskManager       │   position sizing +
                 │   size_order()       │   drawdown circuit breaker
                 └──────────┬───────────┘
                            │ Order | HALT
                            ▼
                 ┌─────────────────────┐
                 │   AlpacaClient       │   paper trading endpoint
                 │   submit_order()     │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌─────────────────────┐
                 │   DecisionLog         │   results/decisions.jsonl
                 └─────────────────────┘
```

`run_cycle.py` orchestrates the loop above. A GitHub Actions cron job (`.github/workflows/weekly-trade.yml`) runs it weekly.

## Tech stack

`Python` · `alpaca-py` (paper trading) · `pandas` / `numpy` · `pydantic` · `pytest` · `GitHub Actions`

## Quickstart

```bash
pip install -e ".[dev]"
cp .env.example .env   # fill in your Alpaca *paper* API keys
pytest                  # all offline, no network calls
python -m tsetlin_trader.run_cycle --dry-run
```

## Trading universe

`SPY` · `QQQ` · `IWM` · `TLT` — liquid, low-cost US ETFs, matched to the strategy set the underlying research (`logic-alpha-tm`) was validated on. The universe is deliberately narrow: proving the paper-trading loop end-to-end on a small, well-understood set comes before any expansion.

## Roadmap

- [ ] Wire in the real `logic-alpha-tm` Tsetlin Machine signal (replacing `MockSignalProvider`)
- [ ] Decision-rationale digest (plain-English rule trace → Telegram/Discord)
- [ ] Live equity curve dashboard, backtest-vs-live drift tracking
- [ ] Volatility-targeted position sizing

## Disclaimer

This project places paper (simulated) trades only. It does not provide investment advice, recommendations, brokerage services, or any assurance of future returns. Nothing here should be construed as financial advice.
