## Unreleased — Aggressive style risks 2% per trade, matching the charter

- Aggressive risked 2.25% of the account per trade, above the 2% ceiling in `TRADING_POLICY.md`. It now risks 2%. Conservative, Balanced and Scalper are unchanged, and a new test fails if any style exceeds the ceiling.
- Backend restart only. No migration, no new environment variables. This lowers the size of new Aggressive entries; open positions are unaffected.
