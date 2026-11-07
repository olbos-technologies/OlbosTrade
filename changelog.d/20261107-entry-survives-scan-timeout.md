## Unreleased — an entry is no longer lost when its scan times out

The equity scan runs under a 90 s guard that cancels the scan wherever it is. Entries were placed inline, so a long scan could cancel an entry that was already at the broker: the order stayed live but no trade row or execution record was written and the position claim stayed `submitted` — the shape of a position later shown as "adopted_untracked".

- Entries now run as their own task (`entry_dispatch.dispatch_entry`); the scan only waits on it, so a scan timeout no longer cancels an order in flight. Applies to the equity scan and both options dispatch sites.
- If an entry is cancelled by anything else after its order was sent, it now keeps the claim as `unknown`, writes the same pending trade row the timeout path writes, logs the execution as `interrupted`, and reports `ENTRY_UNRECORDED stage=cancelled_after_submit` if even that fails.
- A scan timeout now logs how far the scan got: `Scheduler task 'equity_scan' timed out after 90s — skipped (last progress: …)`. The existing greppable prefix is unchanged.

Backend restart only. No migration, no new environment variables. Order, sizing and risk behaviour are unchanged.
