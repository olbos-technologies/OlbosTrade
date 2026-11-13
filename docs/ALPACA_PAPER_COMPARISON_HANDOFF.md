# Alpaca paper comparison setup

Milestone: SaaS-1 account isolation / UX-4 paper workflow, bounded prerequisite to R2 account-specific execution.
Base: c533b572cfe3510d83b8ef960ea032a602eb61de.
Branch: codex/alpaca-paper-comparison.

## Behavior

Account → My Brokers → Compare two paper accounts saves one System / Hybrid
pairing per organization. Roles describe intended policies, not running systems:
A is system TP/SL; B is manual profit-taking with system TP/SL otherwise.

Users create two independent paper accounts in Alpaca, with the same initial
balance, then authorize each separately through existing broker sign-in.
The existing API-key form still supports one active connection per environment;
this feature uses multiple OAuth grants, not API-key replacement.

New storage: `paper_comparisons` (migration 0045), organization PK, grant IDs,
opaque revision, updated timestamp. No credentials or full broker identities
are copied. Same-account selection and foreign/live/revoked grants are rejected.
Concurrent creates/updates have one winner; stale revisions receive 409.

GET/PUT `/api/brokers/oauth/paper-comparison` and POST
`/api/brokers/oauth/paper-comparison/snapshot` use existing authentication,
session ownership, broker entitlement, rate limits on writes/reads, and no-store
response headers. Observation wraps the existing fixed-paper, GET-only snapshot
reader. Reads are sequential on one AsyncSession, not atomic. Changed pairings
are discarded; changed connections lose their earlier observations. Missing or
failed reads are unknown, never zero. No background polling of broker snapshots.

Frontend adds a responsive panel and shares the existing snapshot validator and
inspection component. Source changes are limited to account UI/API client,
OAuth routes, a pairing service/model/migration, tests, and CI gates.

## Validation and deployment

Run frontend `npm test`, `npm run build`, and the Playwright comparison suite.
Backend OAuth coverage gate and PostgreSQL pairing gate are in CI. PostgreSQL
suite must use `OLBOS_TEST_DATABASE_URL`; skips do not count as verification.
Browser captures use synthetic fixtures and are not live account evidence.

Deploy only after green CI and explicit operator instruction. Run Alembic
upgrade head (0045) before starting the new backend; updater does this already.
Requires existing AUTH_ENABLED, broker entitlement, BROKER_ENCRYPTION_KEY and
Alpaca OAuth app configuration. Run `python -m app.broker_paper_preflight` inside
the backend as an offline configuration check. It does not contact Alpaca or
prove credentials/authorization. Never paste keys/tokens into chat or logs.
Keep BROKER=ibkr if the existing operator stack uses IBKR; do not switch the
singleton to Alpaca as a substitute for account-specific routing.

Rollback: restore previous application images; the additive comparison table
can remain. Downgrade 0045 only after stopping new code and backing up its table.
No execution modes, orders, protective exits, broker credentials, or risk limits
are changed by this feature. Not merged or deployed by implementation alone.

## Outstanding before a running A/B experiment

- Operator configures approved Alpaca OAuth app; user creates and authorizes two
  paper accounts. Broker sign-in is read-only today.
- Implement account-bound execution authority/routing and independent exits,
  reconciliation, idempotency, kill controls, and restart/concurrency tests under
  R2 without bypassing its unfinished authorization path.
- Record matched entry IDs, initial risk and manual/system TP/SL attribution;
  account for cash flows/resets and unrelated holdings before comparing returns.
- Run paper end-to-end broker verification and paired-entry/exit checks. No
  running comparison, performance difference, or automated protection is claimed.

References: https://docs.alpaca.markets/us/docs/paper-trading and
https://docs.alpaca.markets/us/docs/using-oauth2-and-trading-api (Nov 4, 2026).
