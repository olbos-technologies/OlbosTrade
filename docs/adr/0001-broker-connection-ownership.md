# ADR-0001 — Broker connection ownership semantics

- **Status:** Proposed — requires operator approval before implementation
- **Date:** 2026-10-05
- **Decides:** `MASTER_ARCHITECTURE.md` §23 item 3 ("organization/account ownership semantics for broker connections")
- **Baseline:** `main` at `b0c59e1`
- **Blocks:** §21 Phase 2 (tenant-aware Alpaca paper execution)
- **Does not depend on:** §23 item 1 (legal operating model) — see "Why this is safe to decide first"

---

## Context

### What exists today, verified against the code

`broker_connections` (migration `0034`) is keyed by **user**, not organization:

```python
# backend/app/models/broker_connection.py
user_id: Mapped[uuid.UUID] = mapped_column(
    UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
)
...
Index("idx_broker_connections_active", "user_id", "broker", "environment",
      unique=True, postgresql_where=(status == STATUS_ACTIVE))
```

There is **no organization, team or tenant model anywhere** in `backend/app/models/`.
`users` is flat: id, email, password hash, `tier`, active flag, timestamps.

A stored connection does not yet route that user's orders. Execution still goes
through a process-wide singleton — `broker_factory.get_broker()`, documented as
"created once at startup", with 52 call sites.

### The repository already holds a tenancy position

This is not a blank-slate decision. `main` carries a deliberate hybrid model:

| Broker | Model | Why |
| --- | --- | --- |
| Alpaca | Many accounts in one deployment, one key pair per account | Stateless REST, authenticated per request (`config.py:29-32`) |
| IBKR | One stack per customer | The gateway is a single logged-in session; `IBKR_CLIENT_ID` multiplexes onto the *same* account, so N accounts need N containers |

`EXECUTION_ENABLED=false` (`config.py:172`) makes a deployment structurally
unable to trade by wrapping the broker in `ReadOnlyBroker` at the factory —
chosen over gating `_execute_signal` because orders also leave through position
rotation's entry and close paths and the kill switch's flattening. That is the
shared tier of the hybrid: signals and research for many, execution elsewhere.

### The conflict this ADR resolves

`MASTER_ARCHITECTURE.md` §7.2 requires `BrokerConnection` to be keyed by
**organization**, with "only one connection … the active execution target for a
given organization and execution scope", enforced by a partial unique index.
§6.1 further requires roles — owner, administrator, trader, approver, viewer,
auditor — and per-organization execution enablement.

None of that is expressible against a `user_id` foreign key. Meanwhile
`docs/trade-desk-2.0/PLAN.md` still lists "Multi-tenant SaaS auth" as a deferred
non-goal, so the three documents disagree about whether tenancy exists at all.

---

## Options considered

### A. User owns the connection (status quo)

Keep `user_id`. No new tables.

- Cheapest today, and honest about a single-operator product.
- Cannot express any §6.1 role. In particular there is no way to separate the
  trader from the approver, so four-eyes approval on a live order is
  unimplementable — and that is a control a broker or counsel is likely to ask
  for, not a nice-to-have.
- No second person can ever see, operate or audit an account. Recovery when the
  owning user is deleted is a `CASCADE` that takes the connection history with it.

### B. Organization owns, users are members (full §7.2 now)

Add `organizations` and `organization_members`, re-key the connection, build the
role matrix and per-org execution enablement.

- Matches the target exactly.
- Requires deciding membership, invitation, billing attribution and role
  semantics up front — several of which genuinely do depend on §23 item 1.
- Largest change to the money path while there is still no validated paper
  track record.

### C. Personal organization per user — recommended

Introduce `organizations` now. Auto-create exactly one per user at registration
and make membership implicit. Key `broker_connections` to `organization_id` from
the start. Roles, invitations and multi-member orgs stay unbuilt.

- Behaves identically to A today: one user, one org, one connection.
- Pays the expensive part — re-keying the connection and every execution-path
  lookup — **once, now**, while there is exactly one connection per user, no
  live trading, and no money depending on the answer.
- Adding a second member later becomes a row plus an authorization check, not a
  migration of the money path.
- The partial unique index keeps the shape it already has, re-scoped:
  `(organization_id, broker, environment) WHERE status = 'active'`.
- Cost if the product stays single-operator forever: one table, one join column.

---

## Decision

**Option C.** An organization owns a broker connection. Every user gets a
personal organization at registration. `broker_connections.organization_id`
replaces `user_id` as the owning key and as the execution-routing key.

The hybrid broker split is unchanged and remains correct: Alpaca connections are
org-scoped inside a shared deployment; IBKR remains a per-customer stack, because
that constraint is in IBKR's gateway, not in this schema. `EXECUTION_ENABLED`
remains a deployment property and is **not** replaced by per-organization
execution enablement — they answer different questions ("may this *instance*
trade at all" versus "may this *tenant* trade"), and §6.1 asks for both.

### Why this is safe to decide first

§23 orders item 1 (legal operating model) before item 3, and that ordering is
right for *membership* semantics — who may join an organization, and whether one
person trading for another is discretionary management, is a question for
counsel. A **personal** organization asserts none of that: it is one user acting
for themselves, exactly as today. It buys the schema shape without taking the
regulated position, so item 1 can be answered later without re-keying anything.

If counsel's answer later forbids multi-member organizations, Option C costs one
unused table. If it permits them, Option C has already done the hard part.

---

## Consequences

**Required**

- New `organizations` table; `organization_members` MAY be deferred while
  membership is implicit, but the FK column must exist from the first migration.
- Backfill: one organization per existing user, then re-point every
  `broker_connections` row. Must be a single migration — a window where some
  connections are user-keyed and some org-keyed is a routing ambiguity on the
  money path.
- The partial unique index is **rebuilt, not edited**. Dropping and recreating it
  in the same migration as the backfill keeps "one active connection per scope"
  true at every point.
- Alembic: next free revision is `0036`. Check `backend/alembic/versions/` before
  numbering — `deploy/hetzner/update.sh` runs `alembic upgrade head`, so a
  duplicate id breaks the deploy, not just the migration.
- Every read of a connection becomes org-scoped. Authorization moves from
  "is this your row" to "are you a member of the owning organization", which must
  be enforced server-side on every such route.

**Deliberately not in scope**

- Roles, invitations, multi-member organizations, per-org billing.
- Replacing the `get_broker()` singleton. This ADR fixes *ownership*; §21 Phase 2
  replaces *routing*. Those are separate changes and separate risks, and doing
  them together would put a schema migration and a money-path rewrite in one
  reviewable unit.

**Risks**

- The backfill touches credential rows. It must move no ciphertext and re-encrypt
  nothing — only the owning foreign key changes.
- `ondelete="CASCADE"` currently destroys connection history when a user is
  deleted. Re-pointing at an organization changes who that cascade follows;
  the revoke-not-delete intent in the model's docstring must survive the change.

---

## Follow-ups this ADR creates

1. `docs/trade-desk-2.0/PLAN.md` lists multi-tenancy as a deferred non-goal in
   **two** places — line 97 ("Multi-tenant SaaS auth") and line 411
   ("Multi-tenant auth/RBAC/billing (SaaS expansion later)"). Supersede both or
   the documents will keep disagreeing; fixing one is worse than fixing neither,
   because it looks resolved.
2. `MASTER_ARCHITECTURE.md` §3.1 warns of a migration `0029` conflict. All eleven
   remote branches carry the same `0029_add_options_scan_rejections.py`; no
   conflict exists in the repository. Reword it before someone hunts for one.
