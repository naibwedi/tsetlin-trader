# Live intraday paper bot

The existing `docs/index.html` is a public, delayed report. `live_paper.py`
is a separate private process with a local dashboard at `http://127.0.0.1:8765`.
It polls Alpaca paper order/position status every 15 seconds. It never calls
the live-money API. The default `observe` mode makes no orders.

## Before paper execution

Create a **second Alpaca paper account** for intraday trading. Its credentials
must differ from the weekly bot's account. Leave the account flat, with no
open orders. Give the always-on host these environment variables through its
private secret store (never commit them):

```
INTRADAY_ALPACA_API_KEY=<second paper account key>
INTRADAY_ALPACA_SECRET_KEY=<matching second paper account secret>
INTRADAY_OPERATOR_TOKEN=<long random operator password>
```

GitHub repository secrets currently power scheduled GitHub Actions. They do
not automatically transfer to a separate always-on host, and Actions cannot
guarantee a timed exit. Configure the three values **once on that host**. The
operator token is needed for the dashboard's pause, resume and flatten
buttons; it is never served to the browser. The service starts **paused**.

Install the pinned dependencies on the host and run from the repository root:

```
pip install -r requirements-lock.txt
pip install --no-deps -e .
python -m tsetlin_trader.live_paper --mode paper
```

The server listens on loopback only. To open it from another computer, use a
private SSH tunnel to the host (`ssh -L 8765:127.0.0.1:8765 user@host`) and
visit `http://127.0.0.1:8765`. Do not publish this port or put the page on
GitHub Pages. For a no-order preview, run `--mode observe`; that mode can use
the existing `ALPACA_API_KEY` and `ALPACA_SECRET_KEY` for market data only.

## Exact behavior

- 10:30–10:40 ET: once per full exchange session, download IEX 5-minute bars;
  require all twelve 09:30–10:25 bars; train the small intraday TM on at least
  30 **prior completed sessions**. Missing/stale bars produce no entry.
- A `cash` signal produces no order. A `buy_SPY` signal submits one SPY market
  order to the dedicated paper account, capped at 5% of account equity and
  $5,000. Existing positions or open orders block entry. The entry intent is
  saved before submission so a restart cannot repeat it.
- The runner queries Alpaca for order status and positions. Submitted and
  pending orders are never labeled filled. It attempts to cancel an outstanding
  entry and close a bot-managed SPY position starting 15:45 ET. Rejected or
  canceled exit orders may be retried until 15:58 ET. The operator's **Close
  bot SPY position** control pauses new entries and requests the same close.
- A 1% decline from equity at the first daily check pauses entries and
  requests a close. Non-bot positions are never liquidated automatically.
- State persists in ignored `data/intraday-live-state.json`. Back it up with
  the host. The service needs an always-on supervisor and manual monitoring;
  software and broker outages can still leave a paper position open.

The TM has **not** shown an intraday return advantage. This service is a
paper-account experiment, not a claim of an investable strategy. Keep the
weekly and intraday reports separate when assessing performance.

