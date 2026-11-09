## Unreleased — Signals status uses the gate's own position cap

- The Signals page said "✓ Clear" using only the trading style's position cap, which cannot see the market-regime tightening the real portfolio gate applies. With a style cap of 5, a high-volatility regime (cap 3) and 3 positions open, ideas showed "Clear" while the gate would refuse them. The status, and the other places that use the same desk context (Command Overview, the equity discovery rail), now take the gate's effective cap and its open-position count from `/api/trade-desk/capacity`, so those ideas show "⊘ Blocked · max positions 3/3".
- If that capacity read fails, the cap is only an estimate, so Signals rows say "? Not checked" (with the existing note) instead of "Clear". The capacity endpoint is unchanged.
- Frontend-only: needs a frontend rebuild, no backend restart, no migration, no new environment variables. No orders, thresholds or gates change.
