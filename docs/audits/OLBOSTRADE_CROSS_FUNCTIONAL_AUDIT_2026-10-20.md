# OlbosTrade — Cross-Functional Repository Audit
Date: 2026-10-20
Scope: Static review of current `main` repository tree, selected implementation files, existing architecture audits, and GitHub Actions job summaries. **Not an exhaustive line-by-line audit, security penetration test, market-data validation, or end-to-end runtime sign-off.**

## Executive decision
**Upgrade rather than rewrite.** Preserve FastAPI/Python research services, broker abstraction, React/TypeScript terminal, PostgreSQL migrations, existing tests, and verified execution gates. Prioritize account/order truth, regression checks for fail-closed behavior, current CI verification, and comprehensible user journeys before adding C++ or more autonomous strategies.

## Coverage and evidence
- Enumerated full `main` Git tree: 889 entries, 818 blobs; approx. 494 backend blobs and 266 frontend blobs.
- Inspected `README.md`, `ARCHITECTURE_MEMO.md`, `ARCHITECTURE_AUDIT.md`, `docs/trade-desk-2.0/PLAN.md`; implementation samples in `backend/app/services/execution_portfolio_gate.py`, `backend/app/services/position_reconciler.py`, `backend/app/api/routes/auth.py`, `backend/app/services/signal_scorer.py`, `backend/ml/features.py`, `frontend/src/utils/navModels.ts`, `frontend/src/components/WelcomeBanner.tsx`, `frontend/src/trade-desk/options/ZeroDteDesk.tsx`.
- Read recent CI run 39508993218: backend-tests SUCCESS; frontend-tests FAIL (Type-check); e2e-tests FAIL (Browser tests), associated with UX PR #123. Root causes not yet established.
- No local build/test execution, broker-connected paper session, device inspection, full source-by-source review, authenticated pentest, or live order was performed.

## Update note (2026-10-22)
This audit is a historical snapshot, not a current release certification. R2-L through R2-O changes subsequently merged into `main` address ledger recovery, paper-reconciliation diagnostics, entry preflight policy, and emergency readiness. These remain separate modules and do not by themselves prove broker execution paths are guarded. R2-P integration work remains under review. The CI and code observations below retain their original dates unless explicitly revised.

## Findings ordered by severity
### P0/P1 — Must resolve before widening automated execution
1. **Portfolio state fail-closed protection needs ongoing regression verification.** The October 20 finding describing a fail-open load path is historical: the current `execution_portfolio_gate.py` defines `PortfolioStateUnavailable` and documents refusal of new entries when required portfolio state cannot be loaded. This update does not independently certify every order-submission route. Continue testing DB timeout, malformed records, lost connection, stale state, recovery, and integration with `_execute_signal`; keep exits separately authorized.
2. **UX PR #123 historical CI failure.** Run 39508993218 failed frontend type-check and browser tests on October 20. PR #123 is currently merged; this old run must not be portrayed as today's CI status. Check the latest commit and current checks before drawing release conclusions.
3. **Order/account truth still needs demonstrated end-to-end verification. DOCUMENTED ARCHITECTURAL GAP, not newly confirmed exploit.** `ARCHITECTURE_MEMO.md` identifies canonical order/fill/position mapping, complete signal→order→fill→exit lineage, margin/buying-power visibility, and broker-event streaming as gaps. Recheck against latest code/migrations: portions may have landed after memo. Run real paper restart, partial-fill, duplicate, reconnect, and multi-account tests and document current coverage before coding replacements.
4. **0DTE automated execution should stay OFF. CONFIRMED product boundary.** `ZeroDteDesk.tsx` is read-only, and `docs/trade-desk-2.0/PLAN.md` explicitly defers 0DTE Autopilot. For 0DTE research improve stale-quote checks, simulated fills, expiry and assignment scenarios, and cutoffs first.

### P2 — Product quality, security and trust
5. **ML quality is not established by model presence.** `signal_scorer.py` uses 15-feature XGBoost return-on-risk scoring, uncertainty band, fallback heuristic and staleness warning. `backend/ml/features.py` documents a constant historical VIX3M term-slope input. Audit feature parity, point-in-time data, model versioning, outcomes and walk-forward net expectancy; compare to naive baselines and fees/slippage. Do not reinterpret regression predictions as win probability.
6. **Simple navigation exists; comprehensive beginner UX does not.** `navModels.ts` already differentiates basic and advanced navigation. PR #123 adds experience-selected welcome guidance only; it does not alter every signal card/portfolio screen. Build shared experience-preference integration, reusable term explanation, accessible risk disclosure, and a preview of simulated outcomes, preserving core information and permissions across levels.
7. **Customer-facing SaaS readiness needs scoped verification.** `auth.py` has authenticated sessions and explicitly no self-service registration; tenant, permissions, billing, privacy, retention, broker-credential storage, rate limiting, audit and incident response must be checked end-to-end before commercialization. Do not assume that presence of individual modules proves production readiness.
8. **Broker/data freshness must be explicitly labeled.** README documents yfinance market display that can be delayed. Validate the actual execution-side quote provenance and timestamp, explain market data latency in UI, and block options execution on stale or unlicensed market data. Confirm IBKR/Alpaca account ownership separation and connection handling under concurrency.
9. **Audit docs can be stale.** Historical architecture docs sometimes describe previously fixed flaws; compare every proposed fix to latest `main` implementation and commit history before opening code changes.

### P3 — Optimize only after above is green
10. **C++ is optional.** Benchmark Python scoring/gate latency against network/broker ACK/fill. Prototype deterministic risk evaluator in paper only if measurable benefit; don't fork OMS or bypass existing controls.
11. **Mobile testing not established.** Execute device-width and browser matrix: small iPhone, modern iPhone, Android, tablet, desktop; login, navigation, signal explanation, paper order preview, rejection messages, risk warnings and session expiry; verify keyboard/screen-reader and minimum touch target sizes.
12. **Commercial validation.** Interview traders for journal/risk/reporting demand and price sensitivity. No assumption of trading profitability or subscription conversion.

## UX roadmap acceptance gates
- UX-1: audited, single nav model and component tokens; no broken links, keyboard focus, contrast tests.
- UX-2: Beginner/Intermediate/Pro persisted; presentation only; no execution-mode/RBAC changes; available beyond welcome banner.
- UX-3: each signal explains instrument, direction, intended time horizon, key evidence, *potential loss*, limitations, staleness, and rejection reason, with collapsible advanced trace.
- UX-4: paper-only guided journey + glossary, payoff scenarios and explicit simulated-vs-live warning; no implied advice.
- UX-5: screenshots at defined viewport sizes, automated a11y checks, manual review with novice and experienced testers; recorded defects and resolution.

## Recommended sequence (independent reviewable PRs)
1. Repair PR #123 CI failures and merge **only if green** and reviewed.
2. Fail-closed portfolio-state policy and comprehensive tests, preserving risk-reducing exits.
3. Verify current order/fill/position identity, recovery and reconciliation with paper E2E.
4. Data provenance, stale quote blocks and expiration protections.
5. Experience levels across read-only views, plus clear signal risk/rejection explanations.
6. Paper practice guide and mobile testing.
7. ML calibration/volatility research; C++ benchmark only if justified.
8. SaaS discovery and product hardening.

## Exit criteria
- No unreviewed live-money path changes.
- Backend, frontend and browser test suites green.
- Broker reconciliation and emergency shutdown proven in repeatable paper E2E.
- Unknown account state and stale price scenarios block new entries.
- All key beginner and professional journeys tested.
- Model performance based on out-of-sample, cost-inclusive results.
- No claim of 'institutional grade' or production readiness until evidence is recorded.

## References
- `backend/app/services/execution_portfolio_gate.py`
- `backend/app/services/position_reconciler.py`
- `backend/app/api/routes/auth.py`
- `backend/app/services/signal_scorer.py`
- `backend/ml/features.py`
- `frontend/src/utils/navModels.ts`
- `frontend/src/trade-desk/options/ZeroDteDesk.tsx`
- `docs/trade-desk-2.0/PLAN.md`
- `ARCHITECTURE_MEMO.md`
- GitHub Actions run: https://github.com/olbos-technologies/OlbosTrade/actions/runs/39508993218
- UX PR: https://github.com/olbos-technologies/OlbosTrade/pull/123
