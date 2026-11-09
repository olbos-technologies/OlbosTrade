## Unreleased — Positions vs Trades (operator diagnostic)

- New read-only System tab, "Positions vs Trades" (`/terminal/system/positions`, operator account only), explaining for each broker holding why the Positions screen shows its strategy as UNKNOWN: row owned by another/unattributed owner, quantity or direction mismatch, pending claim, only closed rows, or no row at all. Backend: `GET /api/admin/position-trades`. Needs a backend restart and frontend rebuild; no migration, no new environment variables. Places no orders and changes no rows.
