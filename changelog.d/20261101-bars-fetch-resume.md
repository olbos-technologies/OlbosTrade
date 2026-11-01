## Unreleased — Bar exporter really resumes, and logs as it goes

- `scripts/intraday_bars_fetch.py` documented that a re-run skips weeks already
  saved, but it re-requested every week and only dropped the duplicate rows,
  costing ~11 seconds a week. It now skips a week that already has bars for that
  symbol (each week is written in one piece, so a week is complete or absent).
- Progress lines are flushed. Redirected output was buffered, so an export that
  died left an empty log and no clue where it stopped.
- Research tooling only: read-only, no trading code, no schema change.
