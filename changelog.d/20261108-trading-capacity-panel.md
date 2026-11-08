## Unreleased — Command Center explains why the desk holds N positions

- New "Trading capacity" panel on the Command Center (desktop and phone): the limit in force, each step that set it (global limit, trading-style cap, market regime), how many positions are open and how much room is left, and what blocked entries in the last 24 hours, grouped by reason.
- It reads the same rule the portfolio gate enforces. The arithmetic moved into `effective_max_positions()` in `execution_portfolio_gate.py`, which the gate now calls; a test pins it to the previous inline formula across caps and regimes, so entry limits are unchanged.
- New read-only `GET /api/trade-desk/capacity` (needs a session). An unreadable open count or block history shows as unavailable, never as zero or "nothing blocked".

Backend restart needed (new route and a small refactor in the portfolio gate), then the frontend rebuild. No migration, no new environment variables. Entry limits and order behaviour are unchanged.
