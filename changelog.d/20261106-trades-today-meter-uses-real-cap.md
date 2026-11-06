## Unreleased — "Trades Today" meters show the desk's real daily cap, not a hardcoded 3

- The Dashboard and Risk page "Trades Today" meters compared the count against a fixed `3`, so a
  normal day read as a breach (4 / 3) while the desk's actual cap (`max_trades_per_day` from
  `/api/guardrails/status`) was 30. Both now use the server's cap.
- When the cap is not reported the meter shows the count as "n / —" in amber with no bar, rather
  than a made-up limit. The Risk page meter also keeps showing a known count when only the cap is
  missing.
- The other meters (loss limits, consecutive losses, open positions) are unchanged and still use
  fixed budgets; the Dashboard's four meters moved into `GuardrailBudgetMeters` so they can be
  tested.

Deploy: frontend rebuild only — no backend change, no migration, no new environment variables.
