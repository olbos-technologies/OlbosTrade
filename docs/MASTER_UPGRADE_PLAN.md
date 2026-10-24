# OlbosTrade Master Upgrade Plan v2.0
**Updated:** 2026-10-20
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
- R1 portfolio state-load failure was fixed by PR #126: `check_execution_portfolio()` now returns `allowed=False` with `portfolio_state_unavailable` on a failed required DB read, and `test_execution_portfolio_gate.py` asserts it. This closes the narrow R1 DB-unavailable issue, not the wider broker-history and entry-authorization program.
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

### R2 — Execution reconciliation and mandatory submission authority [ACTIVE]
R2-A through R2-P introduced ledger evidence, account scoping, paper reconciliation
diagnostics, and entry policy modules. These modules alone did not establish
broker-path enforcement. R2-Q owns the mandatory central order submission boundary.

**Acceptance gate:** no API entry route may directly submit to a broker; an AST
scan must read real API modules and verify permitted closes are explicitly scoped.
Every new entry must require a server-issued, single-use, order/account/org-bound
authorization supported by independently authenticated complete broker history
(PROVEN / UNPROVEN / UNKNOWN), current kill-switch/risk checks, and retry/TOCTOU
protection. UNKNOWN/UNPROVEN deny. Independently position-bound risk-reducing
closes continue. PostgreSQL, negative-mode, concurrency and paper-only tests
must pass, including mutation checks; do not merge/deploy while tests fail.
Until a trustworthy provider exists, all new entries remain held closed.

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
| R1 | Not complete | Portfolio load fail-open needs fix and negative tests |
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
