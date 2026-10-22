# R2 — Broker reconciliation and emergency-exit safety review

Status: audit and acceptance plan; **no production trading behavior changed**.

## Code findings (reviewed on main after R1)

1. `backend/app/services/position_reconciler.py`: `check()` reports broker-only positions, database-only positions, and quantity mismatches as `clean=False`. Broker/DB read errors also return `clean=False` plus warnings.
2. In the same service, `reconcile()` raises for broker-only positions and quantity differences, **but database-only open trades (`phantom_in_db`) produce a warning rather than a hard halt**. Its `ReconciliationResult.clean` calculation does not include `phantom`. This asymmetry needs an explicit product/risk decision and regression tests; it must not be silently treated as agreement.
3. The comparison groups positions by underlying and sums absolute quantities. This is not contract-level proof for options: different expirations, strikes, call/put, long/short legs, or account scopes can share an underlying. A matching underlying total may conceal offsetting or incorrect contracts.
4. `backend/app/services/kill_switch.py` engages a local stop before cancellation/flattening, records unsuccessful cancellation attempts, and re-queries the broker to determine whether exposure is actually flat. A submitted close is not proof of a fill. Verify partial fills, rejected closes, disconnected brokers, and repeated engagement with executable broker mocks.

## R2 release requirements

- Broker truth: compare account ownership and *contract identity*, direction, quantity and lifecycle against DB; distinguish options legs and multi-leg spreads. Unknown data must yield `unverified`, never `clean`.
- Entry restriction: broker/DB read failure, unexpected broker-only position, DB-only active trade, or quantity/contract mismatch must prevent **new exposure-increasing entries** until an explicit resolution, without using a guessed zero.
- Exit availability: authorized risk-reducing exits must have an independently verified path and must not be accidentally rejected by entry-only portfolio checks. Do not submit orders to test against a live account.
- Kill switch: preserve stop state across restarts; detect cancellation failures, partial fills, residual positions, and unavailable broker verification; UI must not claim `flat` without broker confirmation.
- Tenant safety: scope broker and DB reads to the same authorized organization/account; do not flatten or compare another tenant's positions.
- Verification: add deterministic tests using paper/fake brokers for matching positions, phantom DB trades, ghost broker positions, contract-level offsetting mismatch, partial fills, broker disconnection, repeated kill-switch invocation and exits during DB outages.
- CI: require backend tests, PostgreSQL-backed tests, frontend tests and browser E2E checks green. Keep coverage thresholds unchanged. Manual paper-broker drill and operator sign-off before production deployment.

## Recommended implementation order

1. R2-A: implement contract-identity-aware reconciliation and explicit mismatch status; add tests before enabling strict blocking.
2. R2-B: integrate reconciliation status as a fail-closed gate for *new entries*, with tenant-scoped source and bounded freshness.
3. R2-C: verify exit-path exemption and kill-switch outcome reporting via fake/paper broker tests.
4. R2-D: monitored paper-trading rollout, then a separate production decision.

**Safety:** This audit does not change broker execution, trading modes, credentials, or deployment. Findings require code-level validation and operator review before any live enablement.
