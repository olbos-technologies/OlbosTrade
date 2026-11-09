## Unreleased — Setup Readiness moves to the Signals page

- Setup Readiness, the list that shows which watchlist symbols are eligible and, for blocked ones, why, now has its own tab on the Signals page ("Setup Readiness", next to Live Signals). It had no home after the chart page became just the chart.
- The tab has its own address, `/terminal/strat/readiness`, so it can be bookmarked and opened directly, and reload and Back keep you on it. Live Signals is still the tab the Signals page opens on.
- Nothing about the panel itself changed: each blocked or caution row shows its first reason on the row, rows expand by keyboard or click for the full reasons and the unblock condition, and a failed scan says "Setup readiness unavailable" instead of an empty list.

Frontend only: no backend restart, no migration, no new environment variables.
