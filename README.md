# tsetlin-trader

**An interpretable ML trading engine that paper-trades using rule-based signals from a Tsetlin-Machine-family research model.**

Most "AI trading bot" repos are black boxes: a model spits out buy/sell and nobody, including the author, can say why. `tsetlin-trader` is built the other way around. Every decision is logged with the evidence behind it: which Boolean features pushed the model toward the chosen strategy, and by how much. Orders go through a pluggable broker interface, either a zero-setup local simulator or [Alpaca's paper trading API](https://alpaca.markets/). No real money either way.

This repo is the **execution layer**. Signal generation (leakage-aware walk-forward research, Boolean feature engineering) lives in the companion repo [`logic-alpha-tm`](https://github.com/naibwedi/logic-alpha-tm), which this repo installs as a dependency.

> **Status: paper trading only.** Not investment advice. The research repo itself states that its backtests are not evidence of tradable alpha, and that applies here too. Treat this as an engineering and forward-testing exercise.

## How it works

```
 Tiingo EOD prices ──> LogicAlphaProvider ──> RiskManager ──> rebalance ──> BrokerClient
 (SPY QQQ IWM TLT)     fit on labelled days    size + circuit   sells first,   simulated | Alpaca
                       predict latest day      breaker          then buys
                              │                     │                              │
                              └──────────── DecisionLog (results/decisions.jsonl) ───┘
```

1. **Data**: ~5 years of adjusted daily closes for four ETFs from Tiingo. Stale data (more than 5 days old) is rejected rather than traded on.
2. **Signal**: the research pipeline picks one of four strategies: `trend` (SPY or cash), `momentum` (rotate SPY/QQQ/IWM), `defensive` (SPY or TLT), or `cash`. The model is fit on every day whose 20-day forward label is already known, then predicts the latest day. That mirrors one fold of the research backtest, including its 20-day embargo.
3. **Explanation**: for the default Bernoulli model, the lead over the runner-up decomposes exactly into per-feature terms, so each signal carries the top contributing features and their values.
4. **Risk**: positions are scaled to `POSITION_FRACTION` of equity. A max-drawdown circuit breaker liquidates to cash and stays tripped until a human deletes `results/state.json`. Peak equity persists across runs.
5. **Execution**: the account is rebalanced to target, with sells before buys, a no-trade band to avoid churn, and a guard that skips the cycle if orders are still pending.

Example output:

```
signal:   momentum (confidence 0.85, as of 2026-09-17)
          - regime today: SIDEWAYS_LOW
          - 60d momentum leader is SPY (+4.0%)
          - evidence for 'momentum' over 'cash': prior -1.28 + features +2.98
          -   IWM_ret_60>q40 is false -> +0.47
risk:     trade - drawdown 0.00% within limit; scaled by position_fraction=0.25
trade:    sell IWM $9,947.51 (close all)
trade:    buy SPY $19,997.45
```

## Quickstart

```bash
pip install -e ".[dev]"
pytest                                            # offline, no keys needed
python -m tsetlin_trader.run_cycle --broker simulated --signal mock
```

Real signal against your Alpaca paper account:

```bash
cp .env.example .env     # add TIINGO_API_TOKEN, ALPACA_API_KEY, ALPACA_SECRET_KEY, set BROKER_PROVIDER=alpaca
python -m tsetlin_trader.run_cycle --plan-only   # shows the trades, places nothing
python -m tsetlin_trader.run_cycle               # places them
```

Run it once a week, since the strategy selector rebalances on a roughly 5-trading-day cadence. Running it more often is harmless: it rebalances to the same target and does nothing.

## Layout

| Path | Role |
|---|---|
| `signal/logic_alpha_provider.py` | Real signal from `logic-alpha-tm`, with per-feature explanations |
| `signal/mock_provider.py` | Deterministic stand-in for tests and demos |
| `risk/manager.py` | Position sizing and the persistent, sticky circuit breaker |
| `broker/base.py` | `BrokerClient` interface |
| `broker/alpaca_client.py`, `broker/simulated_client.py` | Alpaca paper and local implementations |
| `broker/rebalance.py` | Current positions plus target, turned into trades |
| `run_cycle.py` | Orchestration and CLI |

## Known limitations

- **No unattended schedule yet.** GitHub Actions runners are stateless, so the drawdown state can't persist between runs. The workflow is manual-dispatch only until that's solved.
- **Bernoulli model by default.** It is the interpretable baseline from the research repo. The Tsetlin Machine model (`SIGNAL_MODEL=tmu`) needs the optional `tmu` dependency and gives no per-feature explanation.
- **Prices are Tiingo current-vintage adjusted closes**, a documented limitation of the underlying research.
- **`SimulatedBroker` doesn't move prices**, so it exercises the pipeline but says nothing about P&L.

## Roadmap

- [ ] Persist risk state so the weekly cron can be re-enabled
- [ ] Explanations for the TMU model (clause-level rules)
- [ ] Equity curve and forward-test vs backtest drift report
- [ ] Notifications (Telegram/Discord) with the plain-English rationale

## Disclaimer

This project places paper (simulated) trades only. It does not provide investment advice, recommendations, brokerage services, or any assurance of future returns.
