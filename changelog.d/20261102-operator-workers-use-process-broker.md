## Unreleased — Fill polling and reconciliation work for the designated operator organization

- With `OPERATOR_ORGANIZATION_ID` set (#218), new autopilot trades are stamped with
the operator's organization. The background workers did not know that
organization owns the process broker: they refused to resolve a broker for it
("reconciliation error" on the Risk page, fills never polled), while the legacy
unattributed rows were swept as a second, partial ledger of the same account.

- The designated operator organization now resolves to the process broker. Every
  other organization still needs its own connection; there is no silent default.
- Unattributed rows and the operator's rows are one scope, visited once, and the
  fill poller, reconciler and adoption all see both.
- With no designation set, behaviour is unchanged.

Deploy: backend rebuild only — no migration, no new environment variables.
Optional, user-run: attribute the old unattributed trades to the operator
organization (back up the affected rows first). Not required for this fix.
