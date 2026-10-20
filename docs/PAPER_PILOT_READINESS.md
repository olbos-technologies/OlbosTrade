# Paper-pilot readiness and evidence register

This register governs an **internal, paper-only pilot**. It is not a declaration
that OlbosTrade is institutionally certified or suitable for customer or live
capital. Prove the technical and operating gates below before expanding the
paper pilot. Never use a production approval, rejection, or live order as a
test fixture.

There are two separate decisions: (1) whether this paper pilot may expand
within its defined account and risk envelope, and (2) whether the product may
serve customers or route live capital. Technical evidence supports the first.
The second also needs legal, commercial, broker, and market-data decisions;
passing the paper pilot does not automatically authorize it.

## Current evidence (2026-10-20 repository review)

| Gate | Evidence available | Still required before expanding the pilot |
| --- | --- | --- |
| One execution path | `test_order_submission_inventory.py` pins seven existing broker-submit call sites. The entry path is `_execute_signal`; operator closes and emergency flattening remain separate. | Prove each source's authorization, account/environment binding, risk or risk-reducing exception, idempotency, ambiguous outcome, and reconciliation. Consolidation is **not complete**. |
| Paper order lifecycle | Position-claim, fill-polling, approval-race, reconciler, and kill-switch tests exist. | A recorded end-to-end paper walk for equity and options: intent, decision, submission, acknowledgement, partial/full fill, cancellation, position, and reconciliation. Repeat with duplicate requests, timeout, restart, stale data, and kill switch. No live order. |
| Identity and tenancy | Session and organization-scoping code and tests exist. Production `AUTH_ENABLED` was confirmed true by a boolean-only read on 2026-10-20. | Review all consequential routes, test cross-tenant denial and operator overrides, and require stronger confirmation for high-risk actions. Independent security review remains external. |
| Backup and recovery | `backup_db.sh` now verifies a private dump before publishing it and retains old copies if dump/off-site transfer fails; local failure tests cover both. Production cron was present and local dumps existed through 2026-10-19. | **No off-site target was configured on 2026-10-20.** Configure one, verify receipt, and restore a selected dump into a **disposable isolated database**. Record recovery time and broker reconciliation. Do not restore into production as a drill. |
| Mobile safety | Browser tests cover mobile Home at 320/390/768 px, Signals focus, navigation, unavailable data and kill-switch confirmation. | Manual accessibility audit on iOS Safari and Android Chrome (screen reader, zoom, keyboard, touch, landscape), followed by remediation and a signed operator walkthrough. |

Legal/vendor approval is **not being represented as completed or as a code
task in this internal paper-pilot phase**. Before customer access or live
capital, qualified owners must review the actual operating model, broker and
market-data permissions, disclosures, and agreements. That is a separate
release gate, not an automatic consequence of technical readiness.

## Paper-pilot procedure

1. Record the git commit, migration head, broker account/environment, policy
   version, operator, and date. Confirm **paper** at the broker itself; a UI
   label alone is insufficient. Check the kill-switch state and that no
   unresolved broker/order/position divergence exists.
2. Run the repository tests on the pilot commit. At minimum: backend suite,
   frontend typecheck/unit/browser suite, backup failure tests, and the order
   submission inventory. CI green is necessary, not proof of a broker walk.
3. In a disposable paper account, walk each approved order type from signal
   through reconciliation. Capture redacted intent/decision/order/attempt/fill
   IDs and broker timestamps. Record expected versus observed state at each
   transition; a missing acknowledgement is **unknown**, not rejected.
4. Exercise one fault at a time: duplicate request, request timeout after
   broker accept, backend restart, stale market data, broker disconnect,
   partial fill/cancel, and kill switch. Stop the walk on any unexplained
   order, position, or balance divergence. Never blindly retry an ambiguous
   submit.
5. Restore a backup into an isolated database and compare schema head, row
   counts, and selected redacted records. Reconcile against a broker snapshot.
   Record RPO/RTO and off-site retrieval time. Do not point a running worker at
   the restored database during the drill.
6. Have an operator and reviewer sign the evidence, unresolved risks, and
   explicit **paper-pilot** go/no-go decision. If a gate is unverified, leave
   automation off for the expanded scope. This decision does not approve
   customer or live-capital use.

## Stop conditions

Stop and investigate if the broker environment is uncertain, a submission
outcome is ambiguous, an order or position is unmatched, a protective order is
unverified, auth/tenant scope is uncertain, the kill switch cannot be verified,
or the backup restore/off-site copy fails. Health `200` and a green dashboard
do not override these conditions.

This register is intentionally conservative. Update each row with dated,
redacted evidence links and named owners; do not mark a gate passed from code
presence or a successful unit test alone. See `MASTER_ARCHITECTURE.md` §22 for
the broader institutional completion criteria.
