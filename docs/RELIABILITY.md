# Paper-bot reliability acceptance

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
