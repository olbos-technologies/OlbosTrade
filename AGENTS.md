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
   - **Changelog entries are one new file each, in `changelog.d/`** (`YYYYMMDD-slug.md`; see its README). Do not edit `CHANGELOG.md` — it is the frozen archive. A new file cannot conflict with another new file, which is the point: every branch used to edit the top of one shared file and collided with every other, five times on 2026-10-31 alone. `merge=union` on `CHANGELOG.md` (`.gitattributes`) helps only a *local* merge of a branch that already contains it; GitHub's merge button ignores it, so it is a backstop, not the mechanism. **A PR GitHub calls conflicted (`mergeable_state: dirty`) gets no CI at all** — the workflow never starts — so a PR with no check runs means conflict first, slow runner second; sync with `main` locally. Conflict markers anywhere fail `backend/tests/test_no_merge_conflict_markers.py`. Never extend union to code, where silently concatenating both sides compiles and misbehaves.
8. For touched frontend areas run typecheck, unit tests, browser tests and build; for backend run relevant pytest and critical coverage gates. Report commands and exact pass/fail results; never claim tests were run without evidence.
9. Do not infer production deployment from GitHub merge. Require deployment evidence, health checks, browser verification and rollback capability.
10. Finish with a handoff: milestone ID, base commit, branch, changed files, behavior, checks, risk impact, deployment state and outstanding tasks.

## Environment and verification traps
These are properties of this repository's tooling, not of any one agent, and
each has already produced a confidently wrong conclusion. They are shared
rules for that reason — not Claude-specific notes.

1. **A scan must assert that it read something.** A guard test that silently
   matches nothing is worse than no test: it reports success forever. Assert a
   non-trivial lower bound on what was found, and fail loudly if the thing
   being inspected is renamed or moved.
2. **A safety control must be reachable from the path it protects.** A policy
   module with no caller enforces nothing, however well tested, while reading
   in the changelog as though it does. Prove the call site exists with a guard
   test that fails when it disappears.
3. **No linter or formatter is configured** — no ruff, black, flake8, eslint,
   prettier or pre-commit anywhere. Do not add one, or a config for one, as a
   side effect of another task.
4. **jsdom does no layout and applies no stylesheet.** Any claim about
   geometry, overflow, CSS cascade, computed fonts or appearance at a given
   width is unverifiable under vitest — such tests pass while the UI is
   visibly wrong. Verify in a real browser.
5. **vitest stubs CSS.** `import.meta.glob('**/*.css', { query: '?raw' })`
   returns an empty string for every file while `.tsx` loads normally, so a
   CSS-reading scan written that way passes by reading nothing. Read CSS from
   the backend suite or a real browser instead.
6. **pytest is already quiet.** `backend/pyproject.toml` sets `addopts = -q`,
   so passing `-q` again makes it `-qq`, which suppresses the summary line
   entirely — a check that greps for "N passed" then matches nothing, on a
   fully passing run and an all-skipped one alike. Take counts from
   `--junitxml` and cross-check pytest's exit status.
7. **A skipped test is a green tick that proves nothing.** PostgreSQL-backed
   tests skip unless `OLBOS_TEST_DATABASE_URL` is set, and CI fails the job if
   they skip. Set it locally rather than reading skips as passes.
8. **Confirm a failure against a clean tree before calling it a regression.**
   Some dependencies do not build in every container, so a fresh checkout can
   already fail tests. `git stash` and re-run before "fixing" something that
   was never broken.
9. **Alembic revision ids must be unique.** A duplicate breaks
   `alembic upgrade head`, which the deploy script runs — so the deploy
   breaks, not just the migration. Check the highest existing id in
   `backend/alembic/versions/` first.
10. **Failed reads must not look like safe values.** A swallowed error that
    leaves the last known value on screen presents stale data as freshly
    confirmed. Surface staleness explicitly, in code and in reports.

When reporting results, prefer a command and its output over an adjective:
"0 of 13 modules referenced in `trade_desk.py`" can be checked, "safety
boundary integrated" cannot.

If instructions disagree, prefer the stricter safety boundary and record the discrepancy for review.
