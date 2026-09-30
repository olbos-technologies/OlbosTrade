# Olbos Trade — Claude Code Instructions

## Mission
Improve the existing Olbos Trade application incrementally. Inspect the code and documentation before proposing changes. Do not rebuild working features or claim a capability exists without verifying it.

## Start every task
1. Read README.md, relevant docs, configuration, and tests.
2. Identify the current behavior and the requested behavior.
3. Locate the affected API, UI, database, broker, strategy, and risk boundaries.
4. For non-trivial changes, explain a short implementation plan before editing.
5. Make focused changes and preserve unrelated work.

## Trading safety
- Never submit a live order unless the task explicitly authorizes live execution and the application's approval and risk controls permit it.
- Default new strategies and integrations to simulation or paper trading.
- Preserve existing broker integrations, order lifecycle, reconciliation, approval workflow, kill switch, and risk limits.
- Do not bypass position sizing, allocation limits, market-hours checks, or duplicate-order protection.
- Treat market data and AI-generated signals as untrusted inputs. Never promise returns or signal accuracy.
- Do not log or commit broker credentials, tokens, account numbers, or other secrets.

## Engineering
- Follow the repository's existing stack, conventions, and domain boundaries.
- Keep strategy generation, risk approval, and execution separate.
- Use server-side validation and authorization for consequential actions.
- Make order submission and background processing safe against retries and duplicates.
- Add negative tests for rejected orders, exceeded limits, missing authorization, and broker failures.
- Avoid broad refactors unless required by the task.

## Completion
Run the applicable formatting, lint, typecheck, tests, and build commands documented in this repository. Report what changed, what was tested, what could not be tested, and any live-trading or deployment implications. Never say a feature is production-ready without evidence.

---

# Repository specifics

Everything below is verified against this repository rather than assumed. It exists so the rules above are actionable instead of generic.

## Commands

Taken from `.github/workflows/ci.yml`, so these are what actually gates a merge.

```bash
# Frontend  (cd frontend)
npx tsc --noEmit          # typecheck
npx vitest run            # unit tests
npx playwright test       # browser tests
npm run build             # tsc && vite build

# Backend   (cd backend, Python 3.11)
pytest                    # pyproject sets testpaths=tests, addopts=-q
```

**There is no linter or formatter configured** — no ruff, black, flake8, eslint, prettier or pre-commit anywhere. Do not invent one or add a config as a side effect of another task.

## CI enforces a hard 95% coverage gate

A named set of risk and execution modules must stay at **≥95% coverage or CI fails** — among them `kill_switch`, `guardrails`, `risk_manager`, `position_reconciler`, `account_guard`, `live_tenure_guard`, `margin_monitor`, `trade_frequency_controller`, `allocation_engine`, `options_decision_engine`. See the "New/critical modules — hard 95% gate" step in `ci.yml` for the full list.

Touching one of these means adding tests in the same change. A second job runs whole-app coverage as a ratchet floor.

## Two environment facts that cause false conclusions

**`ta` and `ib_insync` do not build in the cloud container.** A full backend run there fails 8 tests — two each in `test_baseline_comparison_engine`, `test_compute_indicators_adx`, `test_stop_backfill` and `test_backtest_run_equity_route` — and cannot even collect seven more (`test_account_staleness`, `test_backtester_run_equity`, `test_backtester_run_injection`, `test_broker_factory`, `test_data_fetcher`, `test_ibkr_client`, `test_ibkr_execution`). These are pre-existing, and CI installs both successfully. Before reporting a backend failure as a regression, confirm it against a clean tree (`git stash`) — otherwise you will "fix" something that was never broken.

**jsdom does no layout and applies no stylesheet.** Any claim about geometry, overflow, CSS cascade, computed fonts, or how something looks at a given size is unverifiable in vitest — such tests pass while the UI is visibly wrong. Verify in a real browser:

```js
chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' })
```

Never run `playwright install` here. The same blind spot bites CSS-reading tests: **vitest stubs CSS**, so `import.meta.glob('**/*.css', {query:'?raw'})` returns empty strings for every file while `.tsx` loads fine. A scan written that way passes by reading nothing.

## Guard tests that encode cross-cutting rules

These look unusual and are deliberate. Read the docstring before changing or deleting one.

| Test | Rule it protects |
|---|---|
| `backend/tests/test_static_cache_headers.py` | Static-asset cache headers, and the extension list in **two** nginx configs |
| `backend/tests/test_display_font_scope.py` | The serif display face stays on marketing surfaces |
| `backend/tests/test_favicon_assets.py` | Declared icons exist and are the size they claim |
| `frontend/src/utils/__tests__/vocabulary.test.ts` | "Trading style" is never called "Risk profile" |
| `frontend/src/utils/__tests__/noAlphaConcat.test.ts` | No alpha appended to a `var()` colour |
| `frontend/src/hooks/__tests__/navReachability.test.tsx` | Every nav key resolves to a page, and back |

Several live in the backend suite because they check frontend files the frontend's own test environment cannot read. When you add a guard, assert that it reads something — a scan that silently matches nothing is worse than no test.

## Things that have broken before

- **Adding a static asset type.** `.png|.svg|.ico|.webp|.woff2` are listed in `deploy/nginx/olbostrade.conf` *and* the heredoc in `frontend/docker-entrypoint.sh`. An extension missing from either falls through to `location /`, which sets `no-cache, no-store` — the file is then re-fetched on every page load. Add it to both, and to the parametrised list in `test_static_cache_headers.py`.
- **Alembic revision ids must be unique.** A duplicate id makes `alembic upgrade head` fail, and `deploy/hetzner/update.sh` runs it — so the deploy breaks, not just the migration. Check the highest existing revision in `backend/alembic/versions/` before numbering a new one.
- **Colour carries meaning.** `--green`, `--red` and `--amber` signal verified positive/negative, risk state and caution across ~880 usages. Gold (`--brand`) is brand and active navigation only. Colour must never be the only cue for a state.
- **Data cells use `--sans` with `tabular-nums`** (the `.tnum` rule in `index.css`); monospace is reserved for tickers. Columns stop aligning otherwise.
- **Failed reads must not look like safe values.** A `.catch(() => {})` on a status poll leaves the last known value on screen looking freshly confirmed. Surface staleness explicitly.

## Where to read first

- `README.md` — architecture, setup, daily startup
- `TRADING_POLICY.md` — the charter: hard risk limits, supported assets and strategies, trading modes, paper-before-live rules. **Read this before changing anything in the risk or execution path.**
- `docs/ui-ux-phase1-4/PLAN.md` — UI/UX product rules, with a status preamble recording which parts are already implemented
- `docs/runbook.md`, `docs/credential_rotation_runbook.md` — operations
- `ARCHITECTURE_AUDIT.md`, `AUDIT_2026-06.md` — known gaps and deferred work
- `CHANGELOG.md` — update it under Unreleased for user-visible changes

## Deployment

`cd /opt/olbostrade && bash deploy/hetzner/update.sh` — it runs `git pull origin main`, rebuilds `backend` and `frontend` with `--no-cache`, brings the stack up, then runs `alembic upgrade head` inside the `olbostrade-backend` container.

Production is `docker-compose.hetzner.yml`, whose services are `backend`, `frontend`, `caddy` and `olbostrade-db` — container names differ from service names, and `docker compose` commands need the service name. Note `docker-compose.prod.yml` is a different, older stack (`nginx`, `certbot`); do not mix the two up when reading config.

`backend/.env.prod` holds `SECRET_KEY`, the database password, broker credentials and `TRUSTED_PROXY_SECRET`. Never print its contents, paste them into a conversation, or commit them. Filtered output — key names, counts, `grep -c` — is fine.

Say explicitly when a change requires a migration, new environment variables, or an nginx config regeneration. "Frontend-only" and "needs a schema change" are very different deploys to the person running them.
