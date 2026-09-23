# Paper-bot reliability acceptance

## Delivery record — 21 September 2026

Implementation and regression tests are delivered through
[PR #1](https://github.com/naibwedi/tsetlin-trader/pull/1).
Code revision `ab2daee0b05ea112c122ad1501a0bec31f638ae8` passed
124 offline tests locally on Windows/Python 3.12 and the
[Windows/Linux GitHub CI run](https://github.com/naibwedi/tsetlin-trader/actions/runs/35607549804).
Dependency validation (`pip check`) also passed. This delivery record is a
documentation-only follow-up; the PR records the final merge revision.

No brokerage credentials were used, no orders were placed, and no trading
service or paid infrastructure was started during implementation. Merging
disables the old weekly GitHub broker-execution schedule; it does not deploy
the replacement runner. The separate no-order data replay remains scheduled.

Remaining operator work: select the supervised host, configure the dedicated
paper account and private state/backups, verify watchdog/host alert delivery,
then complete observe-only and supervised broker-fill acceptance below.
Neither profitability nor unattended operational readiness is established
by the passing software tests.

This release hardens the software. It does not approve unattended operation,
establish a trading edge, or enable real-money endpoints.

## Changes

- Offline CI on every push/PR, Ubuntu and Windows; tests scrub credentials
  and deny socket connections. Shared test imports now work in CI.
- Kernel-managed locks release on crashes; live owners cannot be overridden
  because their lock file is old. Do not delete lock files.
- Atomic flushed state writes; intraday state is bound to the paper account.
- Restart always pauses new intraday entries. Ambiguous entries and exits
  recover through broker client IDs, never a blind retry.
- Prior-day positions are reconciled before daily references are cleared.
- Flatten intent persists, including when the market is closed. Loss-latched
  entries cannot be resumed through the ordinary resume button.
- Weekly pending orders no longer bypass drawdown evaluation/persistence.
- Sells must fill before buys; available cash is rechecked; unconfirmed buys
  are recorded as execution_pending, not executed.
- An independent read-only watchdog can detect stale/error/late-position state.
- Intraday training/replay and execution share 10:35 entry / 15:45 exit targets.
  Actual latency/fills remain different from bar-open proxies; record both.
- Research dependency pinned to commit 1bebfcdf282ba2acbffff4270d881fdc158bf640.

## Private state and weekly migration

Git is no longer the execution state store. The weekly Actions workflow is
offline/manual only; scheduled broker execution is disabled in this version.
The no-order intraday replay remains separate. Existing public historical
reports are retained, not silently regenerated or treated as new evidence.

Run a single supervised host per account. Set TT_STATE_DIR to a private,
durable directory for the weekly bot. It holds state.json, account.json,
decisions.jsonl and portfolios.json. Automatic public publishing is disabled
for this private path. Back it up and restrict filesystem access.

For a genuinely new, reviewed flat paper account, ALLOW_FRESH_STATE=1 permits
initial binding; remove it immediately afterward. Existing history requires
manual migration: reconcile all broker positions/orders, preserve the actual
peak equity/halt state, and bind account.json to the verified account ID.
Do not reset an existing risk history just to make a refusal disappear.

## Controller and GitHub workflow boundary

The current controller is the deterministic rule blend. Tsetlin Machine and
Bernoulli predictions are shadow observations only and cannot size or submit
orders. Promotion of a model requires forward evidence and a separately
reviewed code change; changing `SHADOW_MODELS` does not promote it.

The broker-free signal-preview workflow is safe on a GitHub-hosted runner: it
receives only `TIINGO_API_TOKEN`, never Alpaca credentials, and never constructs
a broker client. The paper-trade workflow accepts only a manual dispatch with
the confirmation `PAPER` and targets a self-hosted runner labelled
`tsetlin-paper`. Repository variable `TT_STATE_DIR` must point to that host's
private durable state. Missing state fails closed; the workflow never sets
`ALLOW_FRESH_STATE`.

## Required before supervised paper execution

1. Full offline CI green at the exact deployed commit.
2. Separate paper account verified, initially flat with no pending orders;
   no other app/person trading the same symbols in that account.
3. Private state backup and recovery verified. No live-money credentials.
4. Observe-only session confirms completed data, timestamps and signal logging.
5. Supervisor configured; reboot returns paused. One account, one host, one process.
6. Watchdog runs independently; verify real alert receipt. Add an external
   dead-host monitor. Alert delivery is not proven by unit tests.
7. Operator available to reconcile uncertain orders and manually flatten via
   broker UI if automation fails. Never remove state while orders may exist.

## Required before unattended paper operation

Exercise restart-after-submit, submit timeout, broker outage, partial fills,
rejected/canceled exits, stale data, wrong-account state, delayed training,
early close and a missed exit deadline. Verify no duplicate buys and broker
flatness—not merely an accepted order status. Reconcile every supervised
session's orders, positions and realized costs before promotion.

Unresolved intent deliberately stops new activity and requires review. A
market/API outage can prevent an exit. A one-minute watchdog is not a broker
stop order and cannot guarantee a maximum loss. Live trading remains out of scope.
