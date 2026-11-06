## Unreleased — TRADING_CAPITAL_BASE now follows the desk's own gains and losses (new TRADING_CAPITAL_ANCHOR)

- With only `TRADING_CAPITAL_BASE` set, the desk's capital was `min(account, base)`: pinned at the base while the account is above it. A desk that lost money still read as exactly the base, so the **drawdown** and **capital-preservation** checks could never fire (the daily/weekly/monthly loss limits were unaffected, as they sum closed-trade P&L).
- New optional `TRADING_CAPITAL_ANCHOR`: the paper account's net liquidation on the day the base took effect. With both set, the desk's capital is `base + (account now − anchor)` — unrealized included, never above the account, floored at 1. The desk also gets its own persisted peak (`risk_peak_state` row id 2, seeded at the base); the account-level peak row is left untouched.
- Base set without an anchor behaves exactly as before and logs a one-time warning saying so. Neither set is unchanged.
- When the broker is down the fallback is still `STARTING_CAPITAL`, bounded by the base, and does not go through the anchor arithmetic.
- No migration (row 2 is created on first use), no change to the loss limits, position cap, order paths or kill switch. Needs a backend restart; set `TRADING_CAPITAL_ANCHOR` only together with `TRADING_CAPITAL_BASE`.
