# Batch E2 — `trades` isolation: analysis

**Status: analysis only. No implementation in this change.** Everything below is
measured against the code at `origin/main` plus PR #102 (position claims), not
assumed. Counts are reproducible with the commands in [Appendix A](#appendix-a).

Batch E scoped `journal_entries` to an organization and deliberately stopped
there. Its commit says why:

> `trades` is NOT included. It is read by eight route modules … and doing it
> here would produce a change nobody could review.

That estimate was low. The real surface is **22 modules**, and the hard part
is not the routes.

---

## 1. Trade access map

22 modules reference `app.models.trade`: **9 route modules, 12 services, and
`main.py`**. 47 `select(Trade)` call sites in total.

### Read path — 9 route modules (user-facing, need caller scoping)

| module | `select(Trade)` | what it does with trades |
|---|---|---|
| `api/routes/trade_desk.py` | 8 | duplicate guard, open positions, close, fills |
| `api/routes/portfolio.py` | 4 | open positions, exposure, allocation |
| `api/routes/analytics.py` | 2 | aggregate performance (**also 1 write**) |
| `api/routes/risk.py` | 2 | exposure and limit checks |
| `api/routes/paper_trade.py` | 2 | paper position listing |
| `api/routes/journal.py` | 2 | joins trades to scoped journal entries |
| `api/routes/research.py` | 1 | strategy history |
| `api/routes/rotation.py` | 1 | rotation candidates |
| `api/routes/strategy.py` | 1 | per-strategy records |

`journal.py` is the interesting one: its *journal* reads are already
organization-scoped from Batch E, but it joins to `trades` unscoped. Today that
leaks nothing extra, because you only reach a trade through a journal entry you
own — but it is an unscoped read sitting inside a scoped route, and it will
silently become a leak if anyone adds a trade-first query there.

### Write path — only 2 modules

This is the good news and it shapes the whole plan.

| module | writes |
|---|---|
| `services/trade_recorder.py` | 1 `Trade(...)`, 12 attribute mutations, 5 selects |
| `main.py` | 1 `Trade(...)` (the reconciler's adopt path, `approved_by="reconciler_adopt"`) |

**Every trade in the system is created in one of two places.** Stamping
ownership correctly is therefore a two-site change, not a 22-site change. The
22 sites are the *reads*, which is a mechanical (if large) filter addition.

### Background path — 12 services

Classified by how they are invoked, because that determines whether a caller
scope exists at all:

| service | scheduler | request | notes |
|---|---|---|---|
| `trade_recorder` | ✅ | ✅ | **the writer**; both contexts |
| `position_reconciler` | ✅ | ✅ | see §4 |
| `stop_backfill` | ✅ | ✅ | writes stops onto trades |
| `position_rotation` | ✅ | ✅ | reads trades, triggers entries/exits |
| `alpha_edge_engine` | ✅ | ✅ | |
| `orphan_order_sweep` | ✅ | — | background only |
| `trade_excursion_tracker` | ✅ | — | background only |
| `rotation_correlation_cache` | ✅ | — | background only |
| `sector_cache` | ✅ | — | background only |
| `execution_portfolio_gate` | — | ✅ | request only |
| `rotation_preflight` | — | ✅ | request only |
| `live_tenure_guard` | — | ✅ | request only |

---

## 2. Ownership and backfill

### `trades` has no recoverable owner

Columns that look like they might identify one, and why none does:

| column | holds | usable as owner? |
|---|---|---|
| `approved_by` `String(20)` | `"user"`, `"manual"`, `"unknown"`, `"reconciler_adopt"`, `"rotation_review:<id>"` | **No** — a role/source label, never an identity |
| `dispatch_id` | signal dispatch correlation id | No — not linked to a user |
| `strategy_snapshot_id` | FK to `strategy_snapshots` | No — that table has no `organization_id` |
| `trading_mode_at_entry` | `"conservative"` etc. | No |

There is also **no join path** to an organization: `strategy_snapshots` has no
`organization_id`, and nothing links a trade to the `broker_connections` row
(which does) that executed it.

Batch A added an authenticated actor to the *approval payload*
(`approved_by_actor`) but not to the trade row, so even recent trades do not
carry one.

### Consequence: the same conclusion migration `0039` reached

`organization_id` must be **nullable**, with a backfill that assigns only the
unambiguous case (exactly one personal organization ⇒ exactly one person could
have produced the rows) and leaves everything else unattributed with a NOTICE
and a count. Making it `NOT NULL` would force the migration to invent an owner
for every historical trade, and attributing one customer's position history to
another is worse than leaving old rows unowned.

**`NULL` is a scope, not a wildcard.** Reuse `organization_service.owner_scope`
and the `_owned_by` shape from `api/routes/journal.py` verbatim:

```python
if org_id is None:
    return Trade.organization_id.is_(None)   # the single-operator scope
return Trade.organization_id == org_id
```

Writing it as "no filter when `None`" reinstates the shared query — that
mistake is called out in `journal.py:28-30` and is the single most likely way
to get E2 wrong.

### Unlike journal entries, unowned trades are not inert

A journal entry nobody owns is invisible and harmless. An **open trade** nobody
owns is a live position: the fills poller still polls it, the excursion tracker
still tracks it, stop backfill still writes stops to it, and the reconciler
still compares it against a broker. So E2 has to answer a question Batch E did
not: *which organization's background processing adopts an unattributed open
position?*

Recommended: unattributed rows belong to the single-operator scope
(`organization_id IS NULL`), which is exactly what the existing deployment is,
and background workers process that scope by default. Deployments that later
add tenants inherit no cross-tenant processing, because new trades are always
stamped.

---

## 3. Background workers — the actual hard part

14 scheduler jobs run in `_background_scheduler()` with **no authenticated
user**. Those that touch trades cannot call `owner_scope`, because there is no
request and no caller. Three options:

| option | behaviour | verdict |
|---|---|---|
| **A. Per-organization loop** | worker iterates organizations, processing each scope separately | Correct, and the only one that works with per-org brokers (§4). Costs a loop and N× broker calls. |
| **B. Explicit system scope** | worker declares `scope=SYSTEM` and reads across tenants | Simple, and re-creates the shared query under a new name. A bug in one tenant's data then affects all. |
| **C. Leave workers unscoped** | status quo | Not viable: the fills poller would write one tenant's fill onto another's trade. |

**Recommendation: A**, with the scope passed explicitly as a parameter rather
than resolved inside the worker, so a worker cannot accidentally run unscoped —
and so the type signature makes an unscoped call impossible to write by
accident. Workers that are genuinely global (`sector_cache`,
`rotation_correlation_cache` — both derive *market* data, not positions) stay
unscoped, and that should be asserted by a test rather than left to the reader.

A per-organization loop also needs a **failure-isolation rule**: one
organization's broker being unreachable must not abort the sweep for the
others. The `_guarded(...)` wrapper in `main.py` gives per-job isolation today;
E2 needs it per organization within a job.

---

## 4. Reconciliation — where ADR-0001 collides with this

`PositionReconciler.__init__(self, broker: BrokerInterface)` takes **one
broker** and compares its positions against **all** open trades in the
database. Under the hybrid model that is wrong in both directions:

- ADR-0001 makes Alpaca connections **organization-scoped**
  (`broker_connections.organization_id`, unique on
  `(organization_id, broker, environment)`).
- IBKR runs as an **isolated per-customer execution stack**.

So with two tenants, reconciling tenant A's broker against every open trade
reports tenant B's positions as `phantom_in_db` (in the DB, not at the broker)
and could drive an adopt or a halt on data the broker was never asked about.
`untracked_at_broker` has the mirror problem.

**This must change in the same batch as the read scoping.** The reconciler
needs `(broker, organization_id)` as a pair, and its two outputs
(`phantom_in_db`, `untracked_at_broker`) must be computed within one scope.
`main.py:2852`'s adopt path (`approved_by="reconciler_adopt"`) is the second
trade-writing site and must stamp the organization whose broker reported the
position.

The position-claim reconciliation added in #102 has the same shape and the same
need — see §6.

---

## 5. Admin permissions

Current state:

- Authentication is **default-deny** with a path allowlist
  (`api/auth_deps.py`), and routes opt out by path. A test enumerates every
  registered route and fails CI if one is neither protected nor allowlisted.
- Two admin surfaces exist, both under `/api/admin/`:
  `access-requests` (Batch E era) and `position-claims` (#102).
- The allowlist matches on **path only, not method** — which is why the
  operator queue lives on a different prefix from the public form. Any
  trade-admin route must follow that rule.

What E2 needs to decide:

1. **Is there a cross-tenant admin read?** Support will eventually need "show
   me this customer's positions". That is a *capability*, and it should be an
   explicit, audited one — not an absent filter. Recommendation: do **not**
   ship a cross-tenant trade read in E2. Add it later as its own route with its
   own audit row, the way `force_release` is audited in #102.
2. **404, not 403, for a cross-tenant trade id** — matching `journal.py`. A 403
   confirms the row exists, which is itself a disclosure. Ownership belongs
   *in the lookup*, not as a check afterwards, so there is no window where the
   row is loaded before being refused.
3. **No client-supplied scope, ever.** `owner_scope` resolves from the
   authenticated server context; an `organization_id` query parameter would
   hand the isolation decision to the caller.

---

## 6. Interaction with position claims (#102)

#102 ships `position_claims` with a `scope` column that is **always
`'global'`**, and documents why: `trades` has no owner, so the duplicate read
beside the claim is global. Scoping the claim per tenant while that read stayed
global would let the claim admit an entry the duplicate check still treats as a
conflict — the two would disagree.

So the two must move **together**, in this order:

1. `trades` gains `organization_id` (nullable, conservative backfill).
2. The duplicate read in `trade_desk.py` Stage 3 becomes scoped.
3. `position_claims.scope` starts carrying the organization instead of
   `'global'`. The primary key is already `(scope, underlying, asset_class)`, so
   this needs **no schema change** — which is why the column was added now.
4. `claim_lookup` / `claim_reconciliation` become per-organization, pairing each
   organization's broker with its own claims (same change as §4).

Step 3 is a behaviour change worth stating plainly: today two tenants trading
SPY options contend for one global claim, so one of them is refused with
`entry_in_flight`. That is **over-blocking, not a safety hole** — it is also a
cross-tenant information channel (tenant A can infer that someone is entering
SPY), which is another reason not to leave it global indefinitely.

---

## 7. Cross-tenant tests

`tests/test_journal_isolation_pg.py` is the template — 11 tests against **real
PostgreSQL**, because the thing under test is a predicate applied to rows that
exist, and a mocked session makes a scoped query and an unscoped one look
identical. Its cases map almost one-to-one:

| journal test | `trades` equivalent |
|---|---|
| listing returns only your own | per route module, 9× |
| fetching another org's entry is 404 not 403 | trade fetch, close, update |
| a missing id and a foreign id answer identically | same |
| updating another org's entry changes nothing | close / stop-update paths |
| a created entry is stamped with the caller's org | **both writers** (§1) |
| analytics do not aggregate across organizations | `analytics.py`, `portfolio.py`, `risk.py` |
| unattributed rows are invisible to an organization | open-position reads |
| single-operator mode sees exactly the unattributed rows | same |
| missing identity with auth on is 401, not a default scope | all 9 route modules |

Plus cases journal did not need:

- **the duplicate guard is scoped** — tenant A's open SPY position must not
  block tenant B's entry (and vice versa) — real PG, two organizations;
- **the claim is scoped** — the #102 concurrency test, run per organization,
  asserting one entry per `(org, underlying, asset_class)` and *not* one
  globally;
- **the fills poller writes a fill to the right tenant's trade** — the highest-
  consequence cross-tenant write in the system;
- **the reconciler does not report another tenant's positions as phantom**
  (§4);
- **a background worker cannot run unscoped** — structural, in the style of
  `test_only_the_entry_path_consults_the_claim`;
- **genuinely global workers stay global** (`sector_cache`,
  `rotation_correlation_cache`), so the per-org loop is not applied where it
  makes no sense.

---

## 8. Proposed sequencing

Each step is independently reviewable and leaves the system correct:

| # | change | risk |
|---|---|---|
| 1 | migration: `trades.organization_id` nullable + conservative backfill | low — additive, no reads change |
| 2 | stamp ownership at **both writers**; add scoped helper | low — new rows owned, reads still global |
| 3 | scope the 9 route modules + 404-not-403 | medium — largest diff, mechanical |
| 4 | per-organization background workers with explicit scope | **high** — §3, §4 |
| 5 | scope the duplicate read, then `position_claims.scope` | medium — must follow 3 |
| 6 | reconciler takes `(broker, organization)` | **high** — §4 |

Steps 4 and 6 are where this gets genuinely dangerous, because they touch the
fills poller and the reconciler — the two things that decide what the system
believes it holds. They should not ride the same PR as the mechanical read
scoping in step 3.

## 9. Open questions for the owner

1. **Is multi-tenant actually imminent?** Every item above is correct work, but
   steps 4 and 6 carry real risk in the execution path. If the deployment is
   single-operator for the foreseeable future, steps 1–3 plus 5 remove the
   structural hole (no shared queries, claims scoped) and defer the dangerous
   half.
2. **ADR-0002 is still open.** It blocks customer order routing, and steps 4
   and 6 are mostly motivated by customers executing. Doing them before that
   decision may be building for a shape that changes.
3. **Does support need cross-tenant reads at launch?** If yes, it needs its own
   audited route (§5), which is a separate piece of work.

---

## Appendix A — reproducing the counts

```bash
cd backend
# modules touching trades, with read/write counts
python3 - <<'PY'
import pathlib, re
root = pathlib.Path("app")
for f in sorted(root.rglob("*.py")):
    t = f.read_text()
    if not re.search(r"from app\.models\.trade import|models\.trade\b", t):
        continue
    print(f"{str(f.relative_to(root)):52}",
          "select=", len(re.findall(r"select\(\s*Trade", t)),
          "new=", len(re.findall(r"\bTrade\(", t)),
          "attr=", len(re.findall(r"\btrade\.(status|exit_date|pnl|exit_reason|stop_price)\s*=", t)))
PY

# scheduler jobs
grep -nE "await _guarded\(" app/main.py
```
