## Unreleased — Positions tab shows broker orders, who sent them, and stop coverage

- New "Orders at the broker" panel on the Positions tab: every resting order with its sender (This app, TWS / manual entry, or another API client with its id), read from the broker's own record. Senders that are not this app are flagged in words.
- A summary line says whether every open position has a stop at the broker, or names those that don't. A failed or stale read says "Stop unverified" and never shows a position as protected.
- Each equity position row gets a Stop in place / No stop at broker / Stop unverified marker. Colour only reinforces the words and the check or warning mark.
- On phones the orders table stacks into labelled rows, so "Placed by" is never scrolled out of sight.

Frontend only: no backend restart, no migration, no new environment variables. Reads the existing `/api/portfolio/open-orders`; no order, risk or execution code changed.
