## Unreleased — Alpaca paper account comparison setup

- Save two distinct, tenant-owned Alpaca paper sign-in accounts as System / Hybrid intended exit roles in Account → My Brokers → Compare two paper accounts.
- Retrieve separate balances, position counts, and open-order counts with account inspection, partial/unknown states, stale markers, and cleared values after failed reads.
- Pairing is read-only setup: no order routing, automation, matched entries, performance attribution, or winner calculation is enabled. The platform broker account is separate.
- Add migration 0045, revision-checked pairing writes, connection-change checks, PostgreSQL race/isolation tests, and mobile browser coverage.
