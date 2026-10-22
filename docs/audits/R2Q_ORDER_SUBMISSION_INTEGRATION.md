# R2-Q — Actual order-path safety integration map

Status: implementation planning / code inspection only. No broker submission behavior changed.

## Observed entry paths (trade_desk.py)

- `_execute_signal()` documents itself as the single order-entry pipeline and checks `_is_kill_switch_active()`.
- Equity new entries eventually use `ibkr_coordinator.submit(... broker.place_equity_order(...))`, after `position_claim.mark_submitted()`.
- Options new entries use `ibkr_coordinator.submit(... broker.place_order(order))`, after `position_claim.mark_submitted()`.
- The explicit equity close endpoint also calls `ibkr_coordinator.submit(... broker.place_equity_order(...))`; this is **not** a new-entry path and requires independent position-bound authorization. A blanket ban on all submit calls would break necessary closes.
- R2-P's `check_entry_before_submission()` is a pure policy boundary. As inspected, `trade_desk.py` does not import or call this function. **Its merge did not establish execution enforcement**.

## Integration invariants before enabling any order submission

1. Do not accept reconciliation proof from a caller-provided order payload or from an IBKR `fills()` session cache. Obtain an independently authenticated, account- and organization-scoped, durable and complete-history proof.
2. Fail closed on missing, stale, conflicting, incomplete, unscoped, or unreadable reconciliation evidence.
3. Check the current kill-switch state and independently validated broker-account ownership.
4. Evaluate preflight immediately before each actual exposure-increasing submit; additionally protect against proof invalidation between read and submission.
5. Preserve independently verified risk-reducing closes; a `close` label is never sufficient evidence.
6. Cover manual, Copilot-approved and Autopilot entry variants, equity and options branches, retries, and any other broker submission endpoint found by full code search.
7. Persist scoped denial reason and evidence identifier; do not write secrets or sensitive broker payloads to logs.

## Required regression and integration evidence

- With reconciliation unavailable, all new-entry paths refuse to call any broker order placement method.
- Stale and mismatched proofs, multi-account sessions, and unknown kill-switch state refuse entry.
- A verified close can proceed only with position-quantity/account-bound authorization, without widening exposure.
- Concurrent/retried requests cannot reuse stale proof or bypass the gate.
- Paper-broker integration verifies the controls; do not enable live brokerage or 0DTE Autopilot.

## Blocker

No current complete-history proof provider is identified by these inspected modules. **Do not wire a proof flag to `True` or use a permissive fallback.** Implementation must either provide and verify that source first, or intentionally hold all new entries closed until it exists.
