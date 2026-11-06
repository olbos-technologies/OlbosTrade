## Unreleased — An entry that ends without a trade row is now logged and counted

- Observed: the desk's own equity entries showed up as "Adopted from broker", and 14 equity claims stayed `submitted`. Both are what an order that was sent but never recorded looks like, but the code left nothing to say which path caused it: the outer handler of `_execute_signal` wrote a counter and no log line, so an exception after the broker call was invisible.
- Now every path that can end that way — an exception after submission, a fill whose trade row could not be written, a timeout whose fallback pending row also failed (equity and options) — logs one CRITICAL line starting `ENTRY_UNRECORDED` (ticker, stage, order id, claim key, error), increments `execute.unrecorded_entry` (visible in the health snapshot) and records an `unrecorded_entry` event. The outer handler also writes an ordinary error line with the exception type and message.
- Search with `docker logs olbostrade-backend 2>&1 | grep ENTRY_UNRECORDED`.
- Logging and counters only: no change to order submission, claims, results returned, risk gates or the kill switch. Backend restart, no migration, no new variables.
