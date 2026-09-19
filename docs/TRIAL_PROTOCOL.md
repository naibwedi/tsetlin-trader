# Forward trial: frozen decision rule

This trial asks whether the current five-seed Tsetlin strategy selector improves
on two simpler alternatives when all three are evaluated on the same new dates:
the plain strategy blend and that blend with the fixed stress rule. Development
research already failed its earlier gates. This trial collects new evidence; it
does not erase that result or authorize real-money trading.

The bot records its signal after each completed paper cycle. The virtual books
start at 1.0, hold the corresponding target weights, mark to the next available
adjusted closing prices, and charge 2 basis points per dollar of turnover.
`docs/live.json` is generated from the saved decision log. It contains no
account balance, position value, brokerage order ID, or credential. The HTML
page refreshes this committed feed once per minute. It only changes when the
bot runs and the resulting commit becomes available.

## Decision rule, fixed before future observations

Collect at least 52 distinct weekly forward return intervals spanning at least
365 calendar days, with no gap longer than 14 days. When multiple completed
runs use the same ISO week, use that week's latest saved portfolio value. For
the Tsetlin virtual book to pass the **performance screen**:

1. Its final net value must exceed both simple books.
2. For each comparator, the one-sided 95% lower moving-block bootstrap bound
   on mean paired weekly excess return must be above zero. Use four-week blocks,
   2,000 resamples, and fixed seeds 7 and 8.
3. Its maximum drawdown, including the starting value 1.0, must be no worse
   than the plain blend's.

The feed reports `collecting_evidence` until the minimum length is reached,
`performance_gate_not_met` after a failed screen, or
`performance_gate_met_execution_unverified` after a pass. None of these states
switches brokerage mode or grants permission for live-money execution.

## Limits and operational review

The virtual books use adjusted closes and a fixed cost estimate. Sells are
queried for final fill status before buys, but historical records and new buy
submissions may still show pending status. Before any deployment decision,
reconcile every submitted order to a final broker status and compare actual
fills, positions, and costs with the virtual books. Check missed runs and feed
failures. Review robustness under larger
cost assumptions and the historical execution-time difference: the research
audit uses next-close execution while the scheduled bot trades after the open.

Changing the signal model, features, ensemble settings, data source, costs, or
evaluation rule starts a **new trial**. Archive the old feed and label the new
trial explicitly; do not join observations across versions. The 2021–2025
historical holdout remains locked while this forward trial runs.

