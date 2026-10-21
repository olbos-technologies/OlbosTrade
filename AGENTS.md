# OlbosTrade — shared coding-agent instructions
These rules apply to **Cursor, Claude Code, GitHub Copilot, Codex and humans**. Read this file at the start of each task.

## Authoritative references
- Implementation roadmap: `docs/MASTER_UPGRADE_PLAN.md`
- Architecture and safety: `docs/MASTER_ARCHITECTURE.md`
- Trade Desk workflows: `docs/trade-desk-2.0/MASTER_SPEC.md`
- Current code, tests and CI determine what is implemented. Historical docs can be stale.
- Claude-specific repository details: `CLAUDE.md` (do not duplicate divergent safety policies).

## Rules
1. Choose a roadmap milestone and scope before coding; inspect existing code and tests; do not duplicate working components or rewrite the platform.
2. Respect the React/TypeScript/Vite, Python/FastAPI, PostgreSQL and broker-adapter architecture. Preserve navy terminal branding.
3. Beginner/Intermediate/Pro are presentation preferences only. Manual/Copilot/Autopilot are independent execution modes. UI settings must never bypass backend authorization or hard risk controls.
4. Default to read-only/research or paper. Never place live orders, enable 0DTE Autopilot, adjust live risk limits, or deploy without explicit approved instructions and required safeguards.
5. Fail visibly on stale/unavailable critical data. Never convert unknown holdings/exposure to zero or treat uncertain broker responses as fills. Coordinate any risk-gate changes with reconciliation and risk-reducing exits.
6. Maintain tenant/account isolation, auditability, idempotency, request validation and credential secrecy. Never commit secrets or personally identifiable account identifiers.
7. Use feature branches and focused PRs. Do not force-push or overwrite another agent's active work. Resolve merge conflicts against latest `main`.
8. For touched frontend areas run typecheck, unit tests, browser tests and build; for backend run relevant pytest and critical coverage gates. Report commands and exact pass/fail results; never claim tests were run without evidence.
9. Do not infer production deployment from GitHub merge. Require deployment evidence, health checks, browser verification and rollback capability.
10. Finish with a handoff: milestone ID, base commit, branch, changed files, behavior, checks, risk impact, deployment state and outstanding tasks.

If instructions disagree, prefer the stricter safety boundary and record the discrepancy for review.
