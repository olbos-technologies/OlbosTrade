## Unreleased — The reconciler no longer adopts a position that already has a trade waiting for its fill

- A pending trade is an entry the desk placed whose fill has not been recorded yet. The broker already
  shows the position, so the reconciler read it as "untracked at broker" and could adopt it as a second
  trade (`strategy=adopted_untracked`) before the fill poller promoted the original to open: two rows for
  one position, double-counted P&L and risk, two sets of exit logic.
- `_adopt_untracked_positions` now treats a pending trade in the same owner scope as already tracked,
  logs why it left the position alone, and counts it as `reconciliation.adopt_skipped_pending`. Open-trade
  behaviour is unchanged; closed and cancelled rows still do not block adoption; another owner's pending
  trade does not block this owner's; with the operator designation set, the operator's unattributed
  pending trade counts.
- Why it matters now: the scheduler runs the fill poller before the reconciler in each tick, but the
  poller has a 20 second timeout, and the operator organization's workers run for the first time after
  the worker-scope fix (#228). Pending trades created while that organization was skipped are promoted on
  the first poll after deploy; this closes the window where a slow poll could let the reconciler get there
  first.

Deploy: backend rebuild only — no migration, no new variables.
