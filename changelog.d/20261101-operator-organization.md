## Unreleased — Autopilot can be told which organization is the operator

- Production has three personal organizations (one active operator, a
  deactivated account, an unused sign-up), so every autonomous entry since
  2026-10-20 was refused `owner_ambiguous` — 1,476 times. The refusal was
  correct; nothing told the desk who the operator is.
- New optional `OPERATOR_ORGANIZATION_ID`. When set, autopilot enters under
  that organization. It never guesses: a value that is not a UUID, names no
  personal organization, or belongs to a deactivated user still refuses the
  entry (it does not fall back to counting organizations). Unset keeps the old
  behaviour exactly.
- The duplicate-entry guard and the re-entry cooldown treat the *designated*
  organization's view as including the 126 pre-tenancy trades that have no
  owner. Without this, designating an owner would have hidden the open
  position and recent stop-outs from both checks. Other organizations keep the
  strict rule: for them, unowned trades are somebody else's.
- Not changed: risk gates, position cap, order paths, schema. No migration.
  Requires one new line in `backend/.env.prod` and a backend restart.
