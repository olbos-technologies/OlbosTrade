## Unreleased — "Why options aren't trading": a funnel from the scan to an order

- New `GET /api/options/funnel?days=7` (1–90) and a card at the top of the Options Signals page. It
  reads what the system already records at each stage — scan rejections (`options_scan_rejections`),
  qualified signals (`options_signal_history`), the copilot approval queue and execution outcomes
  (`execution_events`) — and shows where candidates die, the biggest reasons at each stage, and a
  one-sentence verdict (`trading`, `nothing_qualifies`, `blocked_at_execution`, `mode_manual`,
  `waiting_for_approval`, `qualified_but_never_dispatched`, `no_data`).
- Reasons are grouped so "below_min_confidence: 0.62 < 0.70" and "…: 0.58 < 0.70" are one reason, and
  the scan's combined per-symbol entry-rule strings are split per strategy. A source-scanning test
  fails if the code starts emitting a rejection reason the funnel cannot name.
- The card also shows the live context that decides the result: execution mode, the regime and
  whether it allows options, and the frequency controller's floors for the active trading mode.
- A failed read is shown as UNAVAILABLE (and the route returns an error), never as a funnel of zeros.
- Counts are attempts, not distinct trades; the regime stage is not recorded per symbol (a regime that
  disallows options scans and records nothing), so the regime is shown separately.

Deploy: backend and frontend rebuild — no migration, no new variables, read-only. It reads existing tables.
