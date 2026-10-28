# Paper account data — SaaS-1 / R2 prerequisite

Branch `codex/broker-paper-snapshot` is stacked on PR #161, at
`9c4fa1c0f0aa38ad8ee77c97dd75bd9dd2ed303e`. PR #161 remains unmerged pending
explicit merge authorization. This follow-up does not change `main` or deploy.

## Behavior

Account → My Brokers → View account data performs an explicitly selected,
read-only Alpaca paper retrieval. Balances, positions and an open-order query
are shown with their availability states, a collection time, and a stale warning
based on a 60-second threshold from the start of collection. A refresh clears
old values first. Switching accounts/closing discards late client responses.

The backend loads the grant by authenticated organization, connection ID,
active status, Alpaca provider and paper environment. It decrypts only that
grant's token, uses fixed HTTPS GET endpoints, and verifies the returned account
identity before positions/orders and again at the end. No platform credentials,
live endpoint, order mutation or automatic retry fallback is used.

Every response has `execution_enabled=false`, `positions_reconciled=false` and
`snapshot_atomic=false`. These are broker reads over an interval, not an atomic
portfolio snapshot, proof of protective stops, complete execution history,
ledger reconciliation or permission to submit an order. They are not wired to
risk eligibility or existing platform dashboard/execution targets.

## Bounds and unavailable data

- Twenty-second total collection deadline; fifteen-second per-request timeout;
  two MiB per response, including streamed/decompressed bytes; no redirects.
- Strict Decimal strings preserve fractional shares and numeric precision. No
  failed or missing quantity/exposure is coerced to zero. Missing optional
  prices/P&L remain null. Raw broker errors, tokens, account numbers and account
  UUIDs are excluded from API output; only account last four characters appear.
- Position rows require unique asset IDs; malformed/unsupported data invalidates
  that section. Positions are capped at 10,000 records; no synthetic aggregation.
- Orders use `status=open&limit=500&nested=true`, preserving nested legs (depth
  <=2, <=32 children, <=2,000 total records). A response reaching 500 roots is
  explicitly incomplete, even if it happens to include every order. No paging
  completeness claim or missing-stop inference is made. Missing optional order
  quantities/fields stay unknown. Unknown order types/statuses are labeled unknown.
- Independent section failure retains successful sections with visible warnings.
  Rejected access anywhere, collection timeout, final account identity/read
  failure, or a local connection change discards all collected values.
- The UI shows at most 100 positions and 100 top-level orders with displayed/total
  counts. This display limit is distinct from the broker order query's limit.

The final conditional health write matches organization, grant, account,
environment, exact encrypted token and prior check timestamp. Disconnect,
reauthorization or another check wins over an older collection and produces
409 with no account data. This guard is shared with PR #161's check endpoint;
no row lock is held during network reads.

## Validation and rollout

Synthetic unit, browser and actual PostgreSQL concurrency tests cover account
scope, failed reads, fractional quantities, nested orders, response limits,
partial results, revoked access, broker-side identity changes and local
connection changes. A real approved Alpaca app and paper-account drill remain
required; tests are not evidence of live connectivity.

There is no new migration beyond dependency PR #161's 0044. No balances,
positions or orders are stored in the database or browser storage by this
feature. Health metadata is persisted through the existing conditional writer.
Rollback the follow-up application code to remove the endpoint/UI; the 0044
schema remains compatible with #161. Apply both code and dependency schema
before deploying. Nothing has been deployed by this work.

Before automated execution: separately consented trading scopes, exact
account/environment routing, complete authenticated history, durable ledger
reconciliation and one-use entry authority, per-account authorization, and real
paper lifecycle/stop/retry/kill-switch drills remain outstanding.

Primary API contracts:
- https://docs.alpaca.markets/us/reference/getaccount-1
- https://docs.alpaca.markets/us/reference/getallopenpositions
- https://docs.alpaca.markets/us/reference/getallorders-1
