# R2-Q Durable Entry Authorization — implementation contract

Status: **NOT IMPLEMENTED / NO GO**. This document is an acceptance contract, not evidence of completed safety. The current `EntryAuthority.request()` always returns `None`, `consume()` always returns `False`, and `order_submission` always denies. Never relax those holds until all requirements below have executable verification.

## Authority and proof source

An entry authorization must be issued only by trusted server-side code using authenticated broker identity and a **complete**, current, scoped execution/order/fill history. UNKNOWN (timeout, partial page, stale cursor, missing account linkage, provider error) and UNPROVEN must refuse. PROVEN alone is insufficient.

Bind the proof to organization ID, principal, broker connection ID, verified broker account ID, paper/live environment, asset class, full canonical order parameters, intended position identity, expiration, and provider evidence version. No client-supplied proof state may be trusted. The existing placeholder digest `unverified:{asset_type}:{ticker}` and scope `unverified` must never qualify.

## Durable transactional state

Proposed migration, subject to database review: a dedicated `entry_authorizations` table with random server-generated token hash (unique), organization/account/connection references, immutable canonical intent digest, evidence reference and observed-at timestamp, risk/kill-switch policy revision, issued-at, expires-at, consumed-at, and state (ISSUED, CONSUMED, EXPIRED, REVOKED). Do not persist bearer secrets in plaintext. Tie the row to the existing position claim and its durable idempotency key.

A single database transaction must conditionally transition `ISSUED -> CONSUMED` where the token hash, account, exact intent, current scope, expiry, and relevant policy revision all match. Require exactly one affected row; racing consumers and replay attempts must fail. A process restart must not restore a consumed permit. Missing DB, lock conflict, or ambiguous result means deny. Record a durable submit intent **before** broker invocation; broker idempotency keys must be preserved where supported.

A database transition cannot atomically encompass an external broker call. Therefore the system also needs reconciliation of the unknown-outcome window: retain unresolved claims, do not retry with a new key, query by broker-recognized client order ID where possible, and never infer 'ABSENT' from missing/partial history. Account-specific submission requirements for equity orders lacking broker idempotency need an explicit unresolved-outcome design.

## Final authorization boundary

The centralized `order_submission` service must be the only entry broker-call site. Re-check authenticated owner/account/environment, kill switch, risk/sizing, duplicate claim ownership, policy revision, and permitted order parameters immediately before consuming the authorization. A passed preparation check is not an entry permit. Do not let the history provider alone grant submission. Risk-reducing close routes require a **separate** position-, account-, side-, and maximum-quantity-bound close authorization; never reuse entry tokens.

## Non-negotiable integration tests

1. Genuine complete, authenticated history and intact risk/account/claim proofs can authorize one **paper-broker stub** submission; broker sees durable intent and its idempotency key before call.
2. UNKNOWN, partial, stale, cross-tenant, incorrect account, policy change, kill-switch flip, changed order parameters, expired permit, or failed DB causes zero broker calls.
3. Two workers simultaneously consuming one token produce at most one broker call; restart, replay, and token forgery do not authorize.
4. Broker timeout with no trustworthy response preserves an unresolved claim and reconciles safely; confirmed durable pending record and recovery paths work.
5. Legacy duplicate/cooldown/liquidity/margin and repricing tests retain their real decisions; successful-submission expectations must not be replaced wholesale with `blocked`.
6. All PostgreSQL tests really run, full backend/frontend/E2E CI passes, and permitted closes still work with independently validated ownership/quantity limits.

**Merge gate:** keep PR #149 draft and live entries disabled until review, migrations, tests, and full CI are complete. The outstanding legacy Trade Desk backend failures are blockers, not expected-green exceptions.
