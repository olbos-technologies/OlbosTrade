## Unreleased — The risk meters show the limits actually in force, and the policy records the new loss limits

- The Dashboard and Risk page meters compared against fixed numbers (daily 2%, weekly 5%, monthly
  10%, drawdown 15%, consecutive losses 3, open positions 5), so they drifted from the real limits
  (and read 4 / 3 trades on a 30-trade desk, fixed earlier). Every meter now reads its limit from
  `/api/guardrails/status`. A limit the server does not report shows as `/ —` with no bar, never a
  made-up number.
- `/api/guardrails/status` now also reports `max_concurrent_positions`: the lower of the global cap
  and the active trading mode's cap, which is what the execution gate enforces (before any regime
  tightening).
- `TRADING_POLICY.md` records the owner's change of the charter loss limits to 5% daily, 10% weekly
  and 20% monthly (paper validation), and documents which settings apply: while
  `PAPER_TRADE_VISIBILITY_MODE=true` the plain `MAX_*` loss settings are ignored and the
  `PAPER_VISIBILITY_MAX_*` ones apply. No limit value changes in code.

Deploy: backend and frontend rebuild — no migration, no new variables. To make 5% / 10% / 20% bite
while paper-visibility mode is on, set `PAPER_VISIBILITY_MAX_DAILY_LOSS_PCT=0.05`,
`PAPER_VISIBILITY_MAX_WEEKLY_LOSS_PCT=0.10` and `PAPER_VISIBILITY_MAX_MONTHLY_LOSS_PCT=0.20`.
