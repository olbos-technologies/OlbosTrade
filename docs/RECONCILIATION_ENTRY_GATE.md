# IBKR reconciliation entry gate

Implemented locally on 2026-11-03; not deployed or verified against production.

## Behavior

Before each IBKR equity/options entry send and each replacement, the adapter
requires a clean reconciliation of the operator account with its own open trades.
The broker's connected position cache is refreshed after the database read.
Non-clean results, errors and a five-second reconciliation timeout veto the send.
Queued orders are checked when they reach the adapter, not only at route entry.
The kill-switch permit is rechecked after the reconciliation await.

Held equity brackets are checked again after parent acknowledgement. If blocked,
the adapter requests parent cancellation without transmitting children; the
existing cancellation/outcome handling decides cancelled versus pending. A
cancellation request alone never proves the broker order cannot fill.

Reviewed risk-reducing operations retain their explicit permit exemption:
closes, emergency flattening, and cancellation do not depend on reconciliation.
The gate neither engages the global kill switch nor closes existing holdings.
Route results distinguish `reconciliation_unresolved` and
`reconciliation_unavailable` from `kill_switch`.

## Scope and limits

- Applies to the process-wide, single-operator IBKR adapter, matching current
  `worker_scope.broker_for_scope` support. This is not an Alpaca/tenant gate or
  proof of correct gateway-account binding.
- Uses the existing reconciler's position matching rules. It does not add
  signed-side, contract-leg or protective-stop reconciliation. IBKR cache
  freshness is not an independently verified exchange snapshot.
- It is a position snapshot check, not atomic broker/DB execution. Accepted
  working orders can still fill later; this change does not cancel them or
  replace the pending-fill recorder. A clean check is not a fill reservation.
- Existing auto-adoption/quantity correction can restore consistency and allow
  the next check to pass. Adoption does not establish historical order origin
  or guarantee stops. No manual-review latch was added.
- Unavailable reconciliation now blocks entries intentionally. No new config
  bypass or migration. Backend rebuild/restart is required to activate it.

## TTWO and CCEP investigation

The supplied screenshot shows unmatched broker holdings, not order history.
No local execution-event/log records establishing their origin were found.
Do not claim they were external orders or late fills without evidence. The
remaining read-only production investigation must correlate broker execution
IDs/order IDs, execution-event timestamps, pending/open/closed trade rows,
recording failures and reconciliation/adoption events for both symbols in the
same account. No live broker or production database was accessed for this change.

## Verification

Fake-broker tests cover first-send vetoes for equities/options, unavailable
snapshots/timeouts, mismatches appearing in a queue or after cancellation,
held-bracket cancellation, close exemption, recovery on a clean check, halt
during reconciliation, and broker-cache changes/failure after the DB await.
Existing order, interlock, route, fill and reconciliation tests are also run.
Tests run in an existing Python 3.11 backend image with network disabled and
the source mounted read-only; no gateway, real orders or dependency installs.

Local result: **171 tests passed**, 100% statement/branch coverage for
`reconciliation_entry_gate.py`, and `git diff --check` passed. The full app suite
and production integration were not run. One existing multipart deprecation
warning was reported.
