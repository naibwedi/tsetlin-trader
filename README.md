# tsetlin-trader

**An interpretable ML trading engine that paper-trades using rule-based signals from a Tsetlin Machine.**

Most "AI trading bot" repos are black boxes: a model spits out buy/sell and nobody, including the author, can say why. `tsetlin-trader` is built the other way around. Every decision is logged with the evidence behind it: the learned rules (clauses) that fired for and against the chosen strategy, and how many votes each carried. Orders go through a pluggable broker interface, either a zero-setup local simulator or [Alpaca's paper trading API](https://alpaca.markets/). No real money either way.

This repo is the **execution layer**. Signal generation (leakage-aware walk-forward research, Boolean feature engineering) lives in the companion repo [`logic-alpha-tm`](https://github.com/naibwedi/logic-alpha-tm), which this repo installs as a dependency.

> **Status: paper trading only.** Not investment advice. The research repo itself states that its backtests are not evidence of tradable alpha, and that applies here too. Treat this as an engineering and forward-testing exercise.

**Visual explainer:** [docs/index.html](docs/index.html) walks through one trading cycle, how the Tsetlin Machine votes, the 2010-2020 test results and the improvement plan.

## How it works

```
 Tiingo EOD prices ──> LogicAlphaProvider ──> RiskManager ──> rebalance ──> BrokerClient
 (SPY QQQ IWM TLT)     fit on labelled days    size + circuit   sells first,   simulated | Alpaca
                       predict latest day      breaker          then buys
                              │                     │                              │
                              └──────────── DecisionLog (results/decisions.jsonl) ───┘
```

1. **Data**: about 18 years (from 2008, matching the research protocol) of adjusted daily closes for four ETFs from Tiingo. Stale data (more than 5 days old) is rejected rather than traded on.
2. **Signal**: the research pipeline picks one of four strategies: `trend` (SPY or cash), `momentum` (rotate SPY/QQQ/IWM), `defensive` (SPY or TLT), or `cash`. The model is fit on every day whose 20-day forward label is already known, then predicts the latest day. That mirrors one fold of the research backtest, including its 20-day embargo.
3. **Model and explanation**: by default a Tsetlin Machine ensemble (5 fixed seeds, votes averaged, so the same data always gives the same signal). Each class's vote is a weighted sum of the clauses that fired, so the strongest clauses for and against the winner are read straight out of the model and sum exactly to its vote (checked in `tests/test_tm_model.py`). Bernoulli Naive Bayes (`SIGNAL_MODEL=bernoulli`) is available as a simpler baseline with per-feature explanations.
4. **Risk, checked first**: the account and the drawdown breaker are checked before any data is downloaded or any model trained, so a broken data feed can never stop the breaker. Positions are scaled to `POSITION_FRACTION` of equity. On a breach the bot liquidates to cash and stays halted until a human deletes `results/state.json`. On Alpaca it refuses to run if that state file is missing (set `ALLOW_FRESH_STATE=1` once to start a history).
5. **Three virtual portfolios**: every executed run also marks three paper portfolios on the same prices with the same costs: the plain strategy `blend`, `blend_filtered` (halved under the simple stress rule from the research repo), and `tm`. State lives in `results/portfolios.json`. Shadow signals from `SHADOW_MODELS` (default `bernoulli`) are logged too. Together these give a fair, real-time comparison nobody could have tuned against.
6. **Execution**: only `SPY/QQQ/IWM/TLT` are ever traded; anything else in the account is reported and left alone. Sells go first and the bot waits for them to fill before buying (buys are skipped if they don't). Every order carries a client id derived from the cycle, so a retry cannot place it twice. A lock file allows one cycle at a time, a no-trade band avoids churn, and pending orders skip the cycle.

Example output (Tsetlin Machine, 2026-09-17, shortened):

```
signal:   cash (confidence n/a, as of 2026-09-17)
          - model=tmu trained on 4587 labelled days (through 2026-08-19), predicting 2026-09-17
          - selected strategy 'cash' (vote margin over 'momentum' +386; 5/5 seeds agree)
          - average votes over 5 seeds: cash +189, defensive -421, momentum -197, trend -429
          - clauses that fired FOR 'cash' (strongest first):
          -   +9.8  IF IWM_ret_60>q20 AND NOT TLT_ret_20>q40 AND NOT TLT_vol_20>q80 AND NOT IWM_vs_SPY_60>q80
          -   +9.2  IF NOT IWM_vol_20>q20
          - clauses that fired AGAINST 'cash':
          -   -10.4  IF TLT_ret_100>q20 AND NOT QQQ_vol_20>q80 AND NOT IWM_ret_5>q60 AND ...
risk:     trade - drawdown 0.00% within limit; scaled by position_fraction=0.25
trade:    sell SPY $24,996.51 (close all)
```

Read `IWM_ret_60>q20` as "small caps' 60-day return is above its 20th percentile of history", and `NOT` as the opposite.

## Quickstart

```bash
pip install -e ".[dev,tm]"
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
| `signal/logic_alpha_provider.py` | Real signal from `logic-alpha-tm`; picks the model and builds the explanation |
| `signal/tm_model.py` | Seeded Tsetlin Machine ensemble that reads its fired clauses back out |
| `signal/mock_provider.py` | Deterministic stand-in for tests and demos |
| `risk/manager.py` | Position sizing and the persistent, sticky circuit breaker |
| `broker/base.py` | `BrokerClient` interface |
| `broker/alpaca_client.py`, `broker/simulated_client.py` | Alpaca paper and local implementations |
| `broker/rebalance.py` | Current positions plus target, turned into trades |
| `portfolios.py` | Blend vs blend+filter vs TM, marked on the same prices |
| `run_cycle.py` | Orchestration, run lock, and CLI |

## Known limitations

- **No unattended schedule yet.** GitHub Actions runners are stateless, so the drawdown state can't persist between runs. The workflow is manual-dispatch only until that's solved.
- **Tsetlin Machine votes are not probabilities**, so a TM signal reports no confidence figure. The clauses are readable but can be long (several conditions joined by AND).
- **The Tsetlin Machine has not beaten the baselines.** On the 2010-2020 development benchmark no model (TM, Bernoulli, logistic, boosted trees) beat the equal-weight blend in any of 7 cost/setting variations, and in the research repo's binary risk-filter pilot the TM passed 0 of 9 seed/cost scenarios while a hand-written stress rule did better. The 2021-2025 holdout stays locked. The TM's demonstrated value here is readable rules, not returns.
- **Alpaca paper trading omits dividends and some execution costs**, so its displayed return is not directly comparable with adjusted-price backtests. The virtual portfolios use adjusted closes for that reason.
- **A run takes about a minute** (5 seeds trained on the full history). The `tm` extra pins `numpy<2`, `scipy<1.13` and `scikit-learn<1.6` because `tmu` requires them.
- **Prices are Tiingo current-vintage adjusted closes**, a documented limitation of the underlying research.
- **`SimulatedBroker` doesn't move prices**, so it exercises the pipeline but says nothing about P&L.

## Roadmap

- [ ] Persist risk state so the weekly cron can be re-enabled
- [ ] Turn clauses into plain-English sentences instead of raw feature names
- [ ] Equity curve and forward-test vs backtest drift report
- [ ] Notifications (Telegram/Discord) with the plain-English rationale

## Disclaimer

This project places paper (simulated) trades only. It does not provide investment advice, recommendations, brokerage services, or any assurance of future returns.
