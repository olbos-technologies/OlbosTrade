## Unreleased — A "Blocked Entries" page to see and release stuck position claims

- New tab under Brokers (`/terminal/system/claims`, nav "Blocked Entries") listing the unresolved position claims that stop the desk entering a ticker: symbol, asset class, state, age, reason, and whether IBKR currently holds the ticker. Equity claims can never clear on their own (IBKR cannot look them up), so until now the only way out was calling the audited release endpoint by hand.
- Release is deliberately slow: one claim at a time (no bulk), a reason of 3–200 characters, a confirmation that IBKR Orders and Portfolio were checked, and the ticker typed in full when IBKR holds it or the position read failed. The server still records who released it and why. The page says it cannot see open orders, only positions.
- A failed read is shown as a failure, never as "no blocked entries"; after a failed refresh the last rows stay, marked possibly out of date.
- Uses the existing `GET /api/admin/position-claims` and `POST /api/admin/position-claims/{token}/release`. No backend change, no migration. Frontend rebuild only.
