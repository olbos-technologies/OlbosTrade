# OlbosTrade Master Upgrade Plan v2.0
**Updated:** 2026-10-25
**Status:** controlled implementation roadmap, not certification of completion.
**Applies to:** human contributors, GitHub Copilot, Cursor, Claude Code, Codex, and other coding agents.

## Authority and precedence
1. The current code, CI results, and broker responses establish **actual behavior**; do not presume documentation is implemented.
2. `docs/MASTER_ARCHITECTURE.md` governs architecture, domain boundaries, and trading safety invariants.
3. `docs/trade-desk-2.0/MASTER_SPEC.md` governs Trade Desk workflows where compatible with the master architecture.
4. This document governs **upgrade sequencing, acceptance gates, and cross-agent coordination**.
5. `docs/ui-ux-phase1-4/PLAN.md` remains historical UI detail; reconcile old task statuses before reusing it.
6. `AGENTS.md` defines tool-independent working rules; Claude, Cursor and Copilot entry points must link to it.
If instructions conflict, do not silently override safety invariants; document the conflict in the PR and request a decision.

## Baseline and state
- Frontend React/TypeScript/Vite; backend Python/FastAPI; PostgreSQL and broker adapters. Preserve working interfaces.
- Onboarding PR #123 merged into `main` at `8dee8245adb0`: experience-level **welcome guidance only**; this is not three application-wide UX modes.
- Audit PR #124 is separate and may not be merged. Check its current status.
- At reviewed main `f4bdf746ee04`, database-load failure already refuses entries (`PortfolioStateUnavailable`); the old DB fail-open finding is resolved in code. Broker position-read failure still fell back to database-only counts. The R1 follow-up on `codex/r1-broker-portfolio-fail-closed` removes that fallback; it is a proposal until merged and separately paper-validated.
- Open PRs #148 and #149 contain entry-history/authorization proposals; #149 is explicitly WIP and deny-all. Do not overwrite those branches or treat them as completed R2 work. PRs #124, #129 and #151 also remain open at this review.
- Code, migrations, dependency versions, and agent rules may evolve. Validate `main` before work; update this ledger in each related PR.

## Non-negotiable product boundaries
- Manual, Copilot and Autopilot are **execution permissions/workflows**, never presentation modes.
- Beginner, Intermediate and Pro are **presentation preferences only**; never increase order, risk or brokerage permissions.
- 0DTE: research and read-only evidence are acceptable; do not enable automated execution or live orders as part of UX or architecture work.
- Unknown portfolio state, ambiguous orders, stale quotes and unsynced broker positions must be made visible, not represented as safe zero exposure. Proposed safety changes must preserve appropriately authorized risk-reducing exits.
- No secrets in repository, logs, screenshots, or PR descriptions. No unattended merge or production deployment without authorized release process.

## Implementation tracks (one reviewable PR per coherent milestone)
### R0 — Release and operational confidence [FIRST]
Verify CI, production deployment path, rollback, account separation, order/position reconciliation and broker-event identity. Record results. Do not declare production-ready from test presence alone.

### R1 — Trading safety [FIRST]
Replace portfolio gate fail-open on state-load failure with refusal of **new risk-increasing orders** when portfolio exposure is unverifiable. Preserve authorized exits and emergency shutdown. Add negative tests for DB errors, stale snapshots, reconnects, retries, partial fills, duplicate orders and ambiguous broker responses. Require paper-only end-to-end validation before rollout.

### R2 — Broker reconciliation and entry authority [AFTER R1]
Track the existing acceptance plan in `R2_BROKER_RECONCILIATION_SAFETY_AUDIT.md`
and integration gaps in `audits/R2Q_ORDER_SUBMISSION_INTEGRATION.md`. Complete
authenticated account/tenant-scoped history, contract-aware reconciliation,
durable single-use authorization, replay/concurrency/restart protection, and
independently authorized risk-reducing exits. Keep existing broker-ordering and
timeout coverage. WIP PR #149 does not implement a successful authorization
path; full CI and a paper-only end-to-end drill remain release gates.

### UX-1 — Design system and information architecture
Reuse current navy terminal tokens and components; simplify basic navigation while preserving Pro tools. Standardize states for loading, stale, empty, error, rejected and delayed market data. Verify 320px layout and accessible focus/contrast/touch targets. Audit existing components before adding new ones.

### UX-2 — Experience-level preferences
Use Beginner/Intermediate/Pro preference shared throughout read-only Home, Markets, Signals, Portfolio and educational panels; keep a reversible switch and accessible labels. Retain identical underlying data, permissions and trading risk controls. Treat the merged PR #123 as onboarding groundwork only.

### UX-3 — Signal explainability
Every signal should show plain-language rationale, supporting indicators, **uncertainty and model limitations**, market-data timestamp, possible max loss when grounded in actual contract economics, and why trades were rejected. Provide expandable technical trace (features, model/version, predicted metric, thresholds). Never label return-on-risk regression as probability of profit.

### UX-4 — Education and simulation
Contextual glossary, guided onboarding and **paper-only** trade walk-through, what-if outcome scenarios with fees, spread, slippage, expiry/assignment assumptions and clear hypothetical labeling. No auto-submission from tutorials.

### UX-5 — Mobile and usability verification
Real-browser iPhone/Android/tablet/desktop matrix; test authentication, data-state errors, navigation, signal explanations, paper preview, rejection workflows, keyboard and screen-reader access. Record feedback from finance novices and experienced traders. JSDOM-only unit tests do not prove responsive rendering.

### ML-1 — Signal science
Validate point-in-time features, train/serve parity, data leakage, VIX-term-slope history, walk-forward net expectancy, baselines, costs, calibration and drawdown. Keep ML separate from hard risk controls; only expand automation after demonstrated paper evidence.

### PERF-1 — Optional C++
Benchmark end-to-end bottlenecks before migration. Prototype isolated risk evaluation only if justified, with parity tests and a Python fallback. Avoid parallel competing order managers.

### SaaS-1 — Commercial readiness
Verify tenancy, RBAC, auth, broker credentials, audit, billing, privacy, data licensing, legal review and tenant isolation before onboarding paying customers. Do not imply regulatory approval or investment profitability.

## Single source-of-truth delivery workflow
1. Sync `main`; read `AGENTS.md`, this roadmap, architecture, relevant feature specs, code and tests.
2. Identify the **single milestone ID** (e.g. UX-3) and exact affected surfaces.
3. Inspect existing implementation; write a small plan and acceptance tests, including regression and rollback considerations.
4. Change only what the milestone requires on a feature branch; never overwrite another agent's unmerged branch.
5. Run frontend typecheck/unit/build, E2E browser tests for UX, and backend tests relevant to affected safety modules; CI must pass.
6. In PR body record milestone, user-visible behavior, affected files, tests, risk/permissions impacts, data provenance, release/rollback, and outstanding issues.
7. After merge, update this plan's milestone status and link the PR. Deployment and production verification are separate from merge.

## Status ledger
| Track | Status | Evidence / next gate |
| --- | --- | --- |
| R0 | Incomplete | CI passes for onboarding PR #123; full production verification not established |
| R1 | Partial; follow-up proposed | DB failure already denies on reviewed main; broker-read denial + recovery/entry-path tests in this branch. Freshness, complete exposure, and paper validation remain open |
| R2 | Incomplete / WIP | PRs #148/#149 are unmerged; authenticated history, durable authorization and complete reconciliation proof remain open |
| UX-1 | Partial / existing components | Inventory and browser verification needed |
| UX-2 | Partial | PR #123 merged; app-wide preference integration needed |
| UX-3 | Partial existing attribution | Standard explanation contract + testing needed |
| UX-4 | Partial glossary/onboarding | Paper guided simulation not verified |
| UX-5 | Not verified | Device/novice/pro test records needed |
| ML-1 | Research baseline exists | Cost-inclusive walk-forward evidence needed |
| PERF-1 | Not prioritized | Benchmark first |
| SaaS-1 | Not certified | Commercial and security gates pending |

## Agent handoff protocol
Every task, regardless of IDE/agent, must leave a PR or handoff note with: `Milestone ID`, `base SHA`, `branch`, `files changed`, `what passed`, `what was not run`, `migration implications`, `trading safety implications`, `deployment status`, `open risks`, `next owner`. A different tool must verify the latest branch rather than trust chat summaries.

## Checked to-do items — 2026-10-25 R1 follow-up

- [x] Review current main and the open PRs; avoid duplicating active R2 work.
- [x] Confirm existing DB-failure denial in code and regression tests.
- [x] Implement broker-read failure denial in this proposed branch, retaining the explicit `portfolio_state_unavailable` response.
- [x] Verify equity/options entry-path denial, empty/populated DB cases, and recovery on a new successful broker read with deterministic mocks.
- [x] Run existing authorized-close and kill-switch regression tests (mock brokers only).
- [ ] Merge after full CI/review and reconcile with the active R2 branch.
- [ ] Record paper-broker disconnect/reconnect and exit evidence before rollout.
- [ ] Finish R0 operational evidence, R2 authorization/reconciliation, UX-1–5, ML-1 and SaaS-1 acceptance gates. PERF-1 remains benchmark-dependent.

A successful `get_positions()` is not proof of freshness, account scope, or
complete risk dollars for untracked positions. This R1 fix closes the exception
fallback only; those broader guarantees remain R2 requirements. No roadmap
track is certified complete by this change.

## R1 follow-up handoff

- Milestone: R1 (broker position-read exception fallback only).
- Base: `f4bdf746ee04e5750837059b5297bb60dd189497`.
- Branch: `codex/r1-broker-portfolio-fail-closed`.
- Changed files: `backend/app/services/execution_portfolio_gate.py`, its regression suite, broker-read prerequisites in `test_batch3_risk.py`, `test_equity_limit_repricing.py`, `test_risk_gate.py`, `test_trade_desk_routes.py`, `test_trade_desk_claim_route.py`, `test_trade_scope_entry_pg.py`, this plan and `CHANGELOG.md`.
- Test migration: those six existing suites now supply successful async broker position responses. Their original submit, repricing, timeout/pending-confirmation, duplicate, cooldown and margin assertions are retained; neither the portfolio gate nor authorization is globally stubbed.
- Targeted verification: 134 passed, zero skips, changed-module coverage 96.18%; 91 integration tests passed, zero skips. The exact critical-module CI command passed 500 tests, zero skips, 96.64% aggregate coverage (this is not 95% for every individual module).
- Red-first evidence: all seven new denial/recovery/route tests failed against the original fallback before the production edit. Clean main full backend run: 2,726 passed, 162 skipped, zero failures; PostgreSQL is unavailable locally, so skipped tests do not establish database correctness.
- Frontend: unchanged; typecheck/unit/browser/build not run locally for this backend-only fix. Full CI and PostgreSQL validation remain required.
- Migration/configuration: none; no new environment variables, schema change or nginx regeneration.
- Risk impact: when the portfolio gate is enabled, a broker-read exception now holds new entries even if DB counts appear safe. Existing gate-disable setting and separately checked close/kill-switch paths are unchanged. Do not disable the gate to work around unavailable data.
- Deployment: not merged, deployed, or enabled against a broker. No live or paper broker orders were placed; all execution tests used mocks.
- Open risks: cached reads may still be stale; untracked risk dollars and account/tenant/contract identity are not completely reconciled. Existing R2 work and paper-broker disconnect/reconnect/authorized-exit drills remain necessary.
- Next owner: reviewer runs full PostgreSQL CI, reconciles with #149 without overwriting it, then the operator records isolated paper evidence before any release decision. Roll back this feature commit only through normal review; rollback restores the prior unsafe fallback and is not a safety mitigation.

- Final local whole-app run: `python -m pytest --cov=app --cov-report=term --junitxml=/tmp/olbos-final-full.xml` returned exit 0: 2,733 passed, 162 skipped, zero failures/errors, 78.58% coverage. Database-backed helpers subsequently received the same explicit async position-read prerequisite; those tests require PostgreSQL CI, not local skips.

## R2-A reporting follow-up — proposed

`codex/r2-phantom-reconciliation-status`, based on main `f4bdf746ee04`, corrects
legacy reconciliation's false clean flag for database-only open trades. The
existing non-raising warning policy is retained. Operator responses and saved
snapshots are verified as `status=warning`, `clean=false`; this is not an entry
authorization or contract-level reconciliation implementation. See
`R2_BROKER_RECONCILIATION_SAFETY_AUDIT.md` for the handoff and remaining gates.
PR #152's independent R1 broker-read fix and WIP PR #149 are left intact.

## R2-A contract evidence validation — proposed

- Branch: `codex/r2-contract-input-validation`; base main `f4bdf746ee04e5750837059b5297bb60dd189497`.
- The read-only contract comparator refuses fractional/string/boolean quantities, blank identifiers, invalid expiration types, and non-finite/non-positive strikes as `invalid_contract_leg`. Invalid evidence is unverified and non-clean; signed aggregation of valid integer quantities remains supported.
- Red-first: 29 failures against original main, including fractional quantity truncation producing a false match. Validation tests cover both evidence sources; valid normalization remains tested.
- Targeted: 56 reconciler/contract/snapshot tests passed, zero skips. Full backend and CI results are recorded in the PR. Frontend untouched; local frontend checks not run.
- Files: contract comparator, contract tests, this roadmap and changelog. No migration, environment-variable, nginx, broker-call, risk-limit, order authorization, merge or deployment change.
- This helper is used by `PositionReconciler.check_contracts()` but remains outside order submission. Complete authenticated history, account/tenant/freshness proof and legacy spread mapping remain open R2 work. PRs #149/#152/#153 remain independent and untouched.
- Next owner: reviewer verifies CI and strict input contract before integration. Rollback restores permissive evidence coercion; paper validation and broader R2 gates remain required before execution integration.


## Combined review and release handoff — 2026-10-25

R0/R1/R2-A: the fixes from PRs #152, #153 and #154 are consolidated on
`codex/combined-reconciliation-fixes`, based on `f4bdf746ee04`. Application
changes combine unchanged; changelog/roadmap conflicts were resolved by
preserving all three entries. No inline review threads were outstanding on
those PRs. Combined CI is the merge gate; original per-PR CI is insufficient.
Historical branch handoffs above describe pre-merge evidence and are retained.
The combined PR supersedes the three separate proposals after merge.

No migration, new environment variable or frontend change. No deployment,
risk-limit adjustment or broker order. A connected, isolated paper broker is
not configured in this workspace, so no broker drill can be claimed. Existing
mocked lifecycle tests are automated evidence, not a paper-broker drill.
PR #149 remains separate WIP; durable authenticated history, authorization,
concurrency/replay/restart evidence and verified exits remain open. Reverting
this combined change restores known unsafe reporting/coercion/fallback behavior
and is not a safety mitigation. CI results and merge SHA belong in the PR.


## R2-B strict proof validation — proposed

- Base `c22522be395f8c8b9483d7ae3373d2bad8cb224b` (merged #155); branch `codex/r2-strict-reconciliation-proof`.
- Require actual boolean verified/clean/scope flags and the expected evidence object. Truthy strings/integers must not establish proof. Malformed timestamp objects return a denial rather than raising.
- Red-first: 27 new cases failed on original policy. Targeted policy/preflight tests: 36 passed, zero skips, 100% statement/branch policy coverage. Full-suite/CI evidence is recorded in the PR.
- Files: policy, policy tests, roadmap and changelog. No schema/config/frontend edits, migration, broker orders, risk-limit change or deployment. Local frontend checks not run.
- Integration: existing order_reconciliation_preflight calls this policy, but neither constitutes durable authorization nor complete-history proof. PR #149 remains WIP; live execution is not enabled.
- Next owner: review CI and integration; complete trusted proof production/consumption and isolated paper-broker evidence. Reverting restores permissive truthiness; not a safety mitigation.

## SaaS-1 broker-hosted onboarding prerequisite — merged, not deployed

Base `df31690f1fce3a65effe7d408925f659c7165554`, branch
`codex/broker-oauth-onboarding`. See `BROKER_OAUTH_ONBOARDING.md` for behavior,
configuration, rollback and remaining execution gates. Adds a broker picker,
Alpaca paper/read-only OAuth, encrypted isolated grants and one-use session-bound
state. IBKR/Schwab remain unavailable. No execution routing, order permission,
mode activation, risk-limit changes, merge or deployment. R2 remains incomplete.

## SaaS-1 account health prerequisite — merged, not deployed

- PR #160 merged as `ca0e9a080948855defafe604167de5f112eea561` after green
  CI run 40632369474. Merge does not establish deployment or real broker proof.
- New branch `codex/broker-connection-health`, based on that merge, adds a
  per-connection paper-only account check, persisted health/timestamp and
  readable reconnect/restriction states. Revision 0044 follows 0043.
- Point-in-time verification cannot grant execution authority or prove holdings,
  open orders, stop coverage or complete history. R2 remains incomplete.
- Conditional writes bind tenant, connection, broker, paper environment, account,
  exact encrypted token and previous observation, so disconnect/reconnect or a
  newer check wins over an older broker response. No locks during broker I/O.
- No order submission, risk-limit or mode changes; no deployment. Registered
  app credentials/approval and a real paper account are still external gates.

## SaaS-1 selected paper account data — merged, not deployed

- PR #161 merged as `9549e4aec1e0c8315d952ff0c29818562a38965f`; PR #162
  merged as `e79e2b103141092dc23f833685aec7f1837d941e`. These merges do not
  establish deployment, real connectivity or execution readiness.
- Add a read-only selected paper-account data view with balances, positions,
  nested open-order results, partial/unavailable/stale states and clear limits.
- Validate broker account identity before/after collection and reuse the
  connection/token/observation conditional write. Never authorize orders from
  these non-atomic reads or infer complete reconciliation/stop coverage.
- No migration beyond dependency 0044, order mutations, risk/mode changes,
  deployment or real broker drill. See `BROKER_PAPER_ACCOUNT_DATA.md` for bounds,
  rollback and the remaining execution requirements.

## SaaS-1 offline configuration preflight — proposed

Base `e79e2b103141092dc23f833685aec7f1837d941e`, branch
`codex/broker-paper-preflight`. Adds a secret-free offline operator command and
paper validation runbook. No database/broker calls, migration, deployment,
execution authorization or real broker evidence. The command validates only
loaded settings; application registration, exact callback registration, schema,
proxy redaction and real isolated paper drills remain external gates.
