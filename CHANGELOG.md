# Changelog

<<<<<<< HEAD
## Unreleased — Collapsed icon-rail hover labels

- Portaled sidebar hover tooltips (and the System menu) so they render beside the collapsed 80px icon rail instead of being clipped by `overflow-x: hidden`. Presentation only; no risk, permission or order-path change.
=======
## Unreleased — Mobile Signals density

- Phone KPI strips use a 2×2 grid and stop mid-word wrapping ("Open positions" no longer reads Ope/n/posit/ions). Capital-at-risk chips are tighter and ENV uses a shorter separator so PAPER/MODE/ENTRIES fit before scroll. Signals workspace title is desk-focused; Find workspace is hidden on Signals pages on phones. Presentation only; no risk, permission or order-path change.
>>>>>>> a2604be (fix(UX-5): mobile Signals density and readable KPI labels)

## Unreleased — Desk-loop sidebar (Bloomberg focus)
## Unreleased — Options rejection log records the real regime

- Fixed: `app/main.py` read `_current_regime.regime_type`, a field `RegimeState` has never defined (its name is `regime`), behind a `getattr` default. Every options scan rejection was therefore stored with no regime, so the rejection report read "BY REGIME (unknown) 100%", and the equity-skip log line always said "regime unknown". Both now read the real field through one helper that raises on a rename instead of returning a default. Diagnostics only; no change to scanning, strategy selection, risk gates or order paths.
- Added a guard test that fails on any `getattr(_current_regime, ...)` naming an attribute `RegimeState` does not define.

## Unreleased — API payload contract guard

- Sidebar keeps the daily desk loop only: Command Center, Markets (Chart/Watchlists), Signals (Alpha Edge / Live / History), Trade Desk (four workspaces), Risk, Journal, Performance, Brokers. Research lab, strategy library/health, scanners and ops extras stay URL-reachable but off the sidebar. Presentation / IA only; no risk, permission or order-path change.
## Unreleased — Signal rationale label parity guard

- Added a guard test asserting the factor labels in `frontend/src/utils/equitySignalRationale.ts` (added by #192) stay in step with the keys `score_equity_signal()` emits, in both directions, and that the two volume labels keep the VWAP condition the engine actually gates them on. Test only; no behaviour change.

## Unreleased — Live Signals plain-language rationale

- Live equity signal cards format the scanner `reasons` map (RSI/MACD/VWAP/…) into "Why it appeared" instead of always showing the empty-producer placeholder. Presentation of existing producer data; no scoring or order-path change.

## Unreleased — Sidebar full tree + icon rail

- Remove Show/Hide advanced from the workspace sidebar; the full nav tree is always available when the rail is labeled.
- Collapsed desktop rail is icons-only (no group letters, submenus, or Advanced chrome); hover tooltips keep the destination name. Presentation only; no risk, permission or order-path change.

## Unreleased — Capital-at-risk strip body align

- Keep the capital-at-risk status strip in the workspace body column (beside the sidebar), with the same 16px horizontal inset as page content, so chips share a left edge with the body rather than spanning over the nav rail. Presentation only; no risk, permission or order-path change.

## Unreleased — Desktop full workspace nav

- Desktop always opens the labeled workspace sidebar (Command Center, Markets, Trade Desk, …). Remove the compact More rail; header toggle still collapses to the icon rail. Phone bottom-nav More is unchanged. Presentation only; no risk, permission or order-path change.

## Unreleased — Markets chart panes (MA + volume + MACD)

- Split the Markets candlestick into price, volume and MACD panes with live MA5/10/20, VWAP and MACD readouts above the canvas (reference-desk layout, navy terminal colors). Taller phone chart so the three panes remain readable. Presentation only; no risk, permission or order-path change.

## Unreleased — Strategies navigation focus

- Keep five daily Strategies destinations: Alpha Edge, Live Signals, History, Strategy Library, Health. Move signal research/calendar/builder/alerts/crypto into Advanced Research and V2 options tooling into Advanced Trade Desk. Presentation only; no risk, permission or order-path change.

## Unreleased — Regime entries chrome honesty

- When the regime reports `equity_allowed=false`, the capital-at-risk row shows **ENTRIES BLOCKED**, the ticker shows **ENTRIES PAUSED**, and the status-bar style lamp is marked gated — so Autopilot/Aggressive preferences cannot be read as live entry authority in a crisis. Footer can show the short git SHA when baked into the build. Presentation only; does not change risk gates or order paths.

## Unreleased — R0 UX deploy handoff

- Record the merged UX batch (#179–#183) at main `30ef70d` and the operator deploy + paper-drill gates in the master upgrade plan. Documentation only; does not deploy or certify R0.

## Unreleased — Reference shell palette v2

- Adopt near-black shell surfaces, restrained gold accents, and a layered Command Center / Home intro background from the approved reference. Presentation only; no risk, permission or order-path change.

## Unreleased — Mobile safe areas

- Opt the viewport into `viewport-fit=cover` and apply notch/home-indicator insets on the terminal shell, with 12px scroll clearance above the phone tab bar. Presentation only; no risk, permission or order-path change.

## Unreleased — Institutional Markets chart polish

- Keep the phone legend as a compact on-plot toggle (not a document-flow disclosure) so candles stay in the first viewport.
- Move catalyst chips above the plot, require 44px timeframe targets, and label the price canvas for assistive tech. Presentation only; no risk, permission or order-path change.

## Unreleased — Mobile Markets chart readability

- Collapse the candlestick legend behind a Legend toggle on phones so the overlay no longer covers the candle pane; desktop keeps the key open.
- Shorten support/resistance axis titles on phones (R/S/E) and tighten the Markets chart header, timeframe row and chart height. Presentation only; no risk, permission or order-path change.

## Unreleased — Mobile workspace cleanup

- Tighten phone chrome and content spacing across Home, Markets, Signals, Positions, Risk, Brokers and More destinations: shorter header/nav, compact search, denser cards and headings.
- Shorten the Home session intro while keeping Positions/Risk actions and failed-read warnings visible. Presentation only; no permission, risk-limit or order-path change.

## Unreleased — Offline history-import staging

- Added durable single-writer quarantine for normalized fixture history pages, scoped report periods, resumable receipts, deduplication and conflict quarantine. Finished imports remain unverified.
- No native Flex parser, authenticated transport, execution-ledger promotion, order authorization or deployment.

## Unreleased — Paper broker setup validation

- Add an offline configuration check with secret-free output and nonzero exit codes for missing or invalid paper OAuth setup. It does not contact a broker or database, enable trading, or certify deployment readiness.
- Add a paper validation runbook and record the merged health/data prerequisites. No migration or new setting.

## Unreleased — Read-only native Flex evidence intake

- Validate a bounded, versioned Activity Flex XML execution subset, preserving scoped contract and decimal financial fields. Reject summary-only data, account mismatches, ambiguous timestamps, visible corrections and conflicting duplicate executions.
- Add an offline safe-summary inspector and synthetic regression tests. Results always remain unverified; no credentials, broker calls, ledger writes, order permissions, schema changes or deployment.

## Unreleased — Workspace style continuity

- Extend navy/gold headings, cards, tabs and mobile layouts to Risk, Journal, Analytics, Backtesting, Research, System and Account pages; align credential screens while preserving the official logo.
- Missing Risk summary/meter values remain unavailable rather than zero; failed Journal reads and Intel service failures show alerts. No broker action, backend risk-limit, permission or deployment change.

## Unreleased — Reference design integration

- Use the established Olbos Trade logo assets in the redesigned terminal header.
- Combine the existing Home/Markets/Signals/Positions redesign with broker onboarding. Align the terminal logo, five mobile tabs, typography and broker cards with the approved navy/gold references. Desk and Risk remain in More.
- Missing or malformed risk state displays unavailable instead of crashing or assuming zero drawdown. No trading permission, migration or deployment.

## Unreleased — UI device-review follow-up

- Phone Positions rows show symbol, quantity, asset type and P&L first; expanding a row reveals the existing details and close-review action. This changes presentation only, not close eligibility or submission safeguards.
- Desktop Home opens the existing analytics section by default while keeping it collapsible. No account data, execution mode or risk calculation changes.

## Unreleased — Strict reconciliation proof flags

- Reconciliation policy rejects malformed evidence objects and non-boolean proof flags. Truthy strings and integers cannot stand in for verified, clean or matching-scope proof.
- Malformed timestamp values produce a denial. This policy change does not enable broker execution or complete R2 authorization.

## Unreleased — R1 broker position-read failure

- New equity/options entries are refused when the portfolio gate cannot read broker positions, instead of falling back to database-only counts. The response remains `portfolio_state_unavailable`; recovery requires a new successful read.
- Existing close paths and kill-switch behavior are unchanged. No migration, environment variable, risk-limit change, or deployment. Complete reconciliation/freshness/authorization and paper-broker validation remain open.

## Unreleased — Reconciliation warning truthfulness

- DB-only open trades now produce a non-clean reconciliation result, consistent with the existing warning status. Operator responses and persisted snapshots no longer pair a warning with `clean=true`, and logs no longer call that book clean.
- The existing non-raising phantom warning policy remains. No new automatic halt, entry authorization, broker operation, schema or configuration change.

## Unreleased — Contract reconciliation input validation

- Read-only contract comparison returns unverified for malformed identity or quantities, including fractional quantities previously truncated to integers. Broker and database evidence use the same validation.
- Valid signed aggregation and normalization remain supported. No order-routing, broker-operation, schema or configuration change.
## Unreleased — Trade attribution report

- Added `backend/scripts/trade_attribution_report.py`: a read-only breakdown of closed trades by strategy, exit reason and regime, with date-clustered bootstrap intervals, a small-cell floor, an explicit refusal to rank strategies whose intervals overlap, cost-drag analysis and a trade-level AUC on `signal_score`. Analysis only; no writes, no orders, no broker calls.

## Unreleased — Paper-pilot reliability gates

- Database backups are written privately to a temporary file, verified before publication, and retain previous copies when dump or configured off-site transfer fails. No schema or environment-variable change.
- CI pins the existing broker-order submission sites for explicit review when a new route is added; this does not assert that all current paths share one OMS.
- A paper-pilot evidence register separates repository tests from broker, recovery, security, accessibility, and legal/vendor sign-off.
- Mobile Home no longer says automatic execution is available while the kill-switch read or broker connection is unverified.

## Unreleased — Command Center and Markets refresh

- Command Center places loss-limit warnings and untracked holdings above the summary. Broker balance and recorded desk-wide P&L have explicit scopes; duplicate headline metrics are removed and empty performance samples show dashes.
- Chart workspace adds focus mode and symbol deep links, labels the model score as research, and uses amber for paused/unknown macro entry state.
- Watchlists gain search, sorting, device-local favorites, ETF labels and chart links. Heatmap tiles fill the workspace with a visible daily-return legend.
- News groups duplicate articles across symbols, adds search/type filtering and visible sources, and labels event importance without implying predicted returns.
- Sector rotation supports ranking by existing return periods and identifies the server rank-change lookback. Status reads no longer imply live quote freshness.
- Frontend-only: no migration, new environment variables or nginx regeneration. Risk limits, order submission, approval, reconciliation and broker execution are unchanged.

## Unreleased — Refined mobile Home

- Phones open a compact Home with broker account value, separately labelled trade P&L, recent/older approval counts, positions, and reliable stop-coverage states.
- Independent reads expose loading, unavailable, and stale data. Queue age is advisory and does not bypass approval validation.
- Stop automation uses the existing Manual-mode API and reports persistence failures. Emergency controls reuse the existing kill-switch confirmation. Desktop dashboard and navigation remain intact.
- Frontend-only: no migration, environment-variable changes, or nginx regeneration.

## Unreleased — Adopted positions are counted as money, separated as attribution

**No migration. No new environment variables.** Performance payloads gain
fields; no existing figure changes meaning.

Measured on production, three months of paper trading:

| strategy | n | total P&L | win % |
|---|---|---|---|
| `equity` (the desk's own decisions) | 43 | **+3,163.51** | 53.5 |
| `adopted_untracked` (inherited from the broker) | 25 | **−4,263.07** | 48.0 |
| combined — the account's actual result | 68 | **−1,099.56** | 51.5 |

- The reconciler's adopt path writes a Trade for every live broker position
  with no DB record, so the guardrails can see it. No strategy chose those
  positions, so **no strategy can be graded on them** — `strategy_health` no
  longer grades `adopted_untracked`, which matters because `meta_strategy`
  reads those grades and would tilt allocation on a score for something
  nobody can choose to trade.
- **Their P&L is real money and stays in every total.** An earlier draft of
  this change excluded it, on the belief that the cost basis was invented.
  That was wrong: the adopt path writes `pos.avg_cost` — the broker's real
  average cost basis — into `credit_received`, which is exactly what equity
  P&L is computed from. Only `entry_date` and the strike fields are
  placeholders, so attribution and any duration statistic are unsafe on
  these rows while the dollars are not.
- Excluding them would have reported **+3,163 on an account that was down
  1,099**: a sign flip on the number the desk steers by, in the direction
  that reads as "we are fine" while capital drains. A real-PostgreSQL test
  on `/api/paper-trade/portfolio` now pins the inclusive total, because that
  endpoint builds its own SQL and the unit tests did not cover it — which is
  how the bug reached a branch in the first place.
- `compute_performance` and `/api/paper-trade/portfolio` now report the
  split alongside the totals: `attributable_trades`, `attributable_pnl`,
  `adopted_positions`, `adopted_pnl`, `adopted_present`, and
  `adopted_closed` / `adopted_closed_pnl` respectively. The desk's decisions
  and the account are different questions, and on this book they gave
  opposite answers.
- **Nothing is dropped silently**, the way `unmeasured_trades` already
  travels with performance and `heat_overstated` with portfolio heat.
- **Risk still counts them.** Exposure, portfolio heat, concurrent-position
  limits, reconciliation and the kill switch are untouched. A guard test
  pins that those modules never learned about this distinction.

## Unreleased — Equity exit geometry can be swept instead of guessed

**No migration. No new environment variables. No change to any shipped
parameter or to any execution path.** `equity_stop_atr_multiplier` (2.0) and
`equity_target_atr_multiplier` (4.0) are untouched; this adds the tooling to
choose them from a backtest, and nothing more.

- Production equity exits over 2026-08-01 → 2026-10-17, adopted positions
  excluded (43 attributable closes), realised a reward:risk of **0.31**
  against a geometry that plans **2.0**. Converting each group to ATR units
  via `planned_risk = 2 x ATR x quantity`: trades held any length of time
  peaked at **1.12 x ATR** (manual, 8.5 days) and **1.14 x ATR**
  (closed-at-broker, 13.2 days) — independently, within 2% of each other —
  while the target sits at **4.0 x ATR** and the stop at 2.0 x ATR is
  reached, paying exactly −2.00 x ATR when it is (−$1,717.42 realised
  against −$1,715.70 planned). An unreachable target against a reachable
  stop is negative expectancy from the geometry alone, independent of
  whether the entry signal has any skill.
- That is an inference from 43 trades and far too few to conclude anything,
  which is the point: `app/services/equity_exit_optimizer.py` grid-searches
  both multipliers against real `Backtester.run_equity()` calls on a
  training window, then validates the winner strictly out-of-sample.
- **This fills an injection point that had been inert since it was added.**
  `run_equity(..., equity_params=)` was documented as waiting for "an
  eventual equity-side optimizer"; nothing had ever passed a non-default
  value.
- Acceptance diverges from `strategy_optimizer`'s percentage gate on
  purpose: percentage improvement over a baseline whose Sharpe is near zero
  or negative is unstable to the point of meaninglessness, and the baseline
  this was built for is losing money — the expected case, not an edge case.
  The gate is an absolute Sharpe gain, which stays interpretable through
  zero, plus a train-to-validation decay check.
- Scoring excludes `run_equity`'s force-closed `backtest_end` row, which
  carries a hardcoded `pnl = 0.0`. Counting it as a real break-even drags
  every mean toward zero, and worst for exactly the wide-target geometries
  under test, which leave positions open longest.
- Targets nearer than the stop are in the grid deliberately. It looks wrong
  and it is the hypothesis.
- `scripts/equity_exit_sweep.py` runs it. **Read-only** — writes no config,
  no row, submits no order, and opens no broker connection: OHLCV comes from
  yfinance through a stub broker, so a research run cannot touch the live
  execution stack or consume an IBKR client id. A guard test pins the
  absence of `get_broker`.
- **No `max_position_pct` axis.** That cap is a kwarg on
  `compute_equity_trade_plan()`, not a field on `EquitySignalParams`, and
  `run_equity` does not thread it — so it stays at the production default of
  0.08 for every grid point. That is the regime being measured: all seven
  production stop-outs sized to ~$20k notional, i.e. the cap bound on every
  one, which is why realised risk floated between $1,052 and $2,223 while
  `risk_pct_per_trade` (0.02, a $5,000 budget) never got a say — it would
  need a 25% stop distance to bind, and ATR stops are 5–11%.
- The sweep recommends; it does not act. A person decides whether to move a
  production parameter, with the trade counts in view.


## Unreleased — Background workers run per owner (Batch E2 steps 4 and 6)

**No migration. No new environment variables.** No visible change in
single-operator mode: the sweep visits one scope (`organization_id IS NULL`)
against the process broker, which is exactly what ran before.

- 14 scheduler jobs run with no authenticated user, and the ones touching
  trades compared **one** broker against **every** open trade. The fills
  poller decides whether a position is still held, so unscoped it would close
  tenant B's trade — and write a P&L — because a position was absent from
  tenant A's broker, where it never was. The reconciler would report B's real
  positions as `phantom_in_db` and A's as `untracked_at_broker`, either of
  which can drive an adopt.
- `worker_scope.for_each_scope` now resolves a `(broker, owner)` pair per
  owner and isolates failures per owner, so one unreachable broker no longer
  ends the sweep for everyone. Skipped (no broker) and failed are counted
  separately — they must not look alike in a log.
- `PositionReconciler(broker, *, organization_id)` — required keyword, no
  default. An unscoped reconciliation no longer constructs.
- **The reconciler's adopt path now stamps the owner whose broker reported
  the position.** It wrote NULL until this pairing existed, with a comment
  saying why it could not do better.
- The position-claim sweep is per owner too. A release happens when the
  broker says ABSENT, and a broker never asked about a claim says ABSENT —
  so an unscoped sweep would hand an in-flight position back to the
  duplicate guard as free, re-opening the race #102 closed.
- **An owner with no resolvable broker is skipped, never defaulted to the
  operator's broker.** IBKR cannot be per-organization in this process at all
  (single logged-in session), so such an owner is skipped rather than read
  against the operator's own account.
- **Still on the operator's own broker, deliberately:** `stop_backfill` and
  `orphan_order_sweep`. They place and cancel orders, and pointing them at a
  customer's connection would mean a timer trading in that account. A guard
  test pins it. This is the remaining owner decision.
- `sector_cache` and `rotation_correlation_cache` stay global — they derive
  market data, which belongs to no tenant — and a test asserts it.

## Unreleased

### `update.sh` no longer pulls — deploying is two stages

**Operator-facing change to the deploy procedure. No migration, no new
environment variables.**

- `deploy/hetzner/update.sh` ran `git pull origin main` as its first step,
  rewriting the file bash was executing. Bash reads a script in chunks and
  seeks back for more; git replaces the file in place, so the next read came
  from a different, longer file at a stale offset. Reproduced at the exact
  sizes involved (4501 → 6328 bytes): steps 2–5 never ran, garbage commands
  executed, exit 127 — a deploy that silently skipped its own migration step.
- The pull moved out of the script. Deploys are now
  `git pull --ff-only origin main` **then** `bash deploy/hetzner/update.sh`.
- Step 1 of the script instead fetches and compares, and **refuses to deploy**
  (exit 1, before anything is built or restarted) when `HEAD` is not
  `origin/main`. It prints both SHAs and the remedy, and prints the SHA it is
  deploying when current.
- Three guard tests in `test_update_script_deploys_in_order.py` pin it: the
  script never pulls, a stale checkout refuses, a current one proceeds.

### Trades are scoped to their owner (Batch E2 steps 1, 2, 3, 5)

**No migration beyond 0041, which arrives with this work. No new environment
variables.** Behaviour change for an operator running with
`AUTH_ENABLED=true`; no visible change in single-operator mode, where the
scope is `organization_id IS NULL` and that matches every existing row.

- Nine route modules read `trades` through shared, unscoped queries. They now
  filter on the caller's organization via `trade_scope.owned_by`: open
  positions, trade history and its total, portfolio heat, open orders,
  correlation, rotation performance, scenarios, portfolio state, performance
  and mode analytics, strategy health, and the research assistant's context.
- **Closing someone else's trade answers 404, not 403.** Ownership is part of
  the lookup rather than a check after it, so a foreign id and a nonexistent
  one are indistinguishable — a 403 confirms the row exists — and no order is
  submitted on the way to the refusal.
- Several of these handlers wrap their work in `except Exception`, which
  catches `HTTPException`. Each scoped handler now re-raises it, so a 401
  cannot come back as an empty, successful-looking payload. In a
  single-operator deployment the rows it would otherwise have returned are
  every open position.
- `/portfolio/open-orders` establishes the caller **before** reading the
  broker-wide order book, not after.
- **The risk gate deliberately stays desk-wide.** `TRADING_POLICY.md`'s limits
  are caps on one broker account; dividing them per tenant would multiply real
  exposure by the tenant count. The displays of those numbers are scoped, so
  they can read lower than the figure that blocks an entry.
- Still unscoped, deliberately (Batch E2 steps 4 and 6): fill polling,
  excursion tracking, stop backfill and position reconciliation process every
  row regardless of owner.

## Unreleased — The duplicate-position guard is no longer a bare read

**Requires a migration (0040). `deploy/hetzner/update.sh` now applies it from
the newly built image before any new container serves — see docs/runbook.md.
No new environment variables.**

- `_execute_signal` Stage 3 asked the database whether an open or pending
  trade already existed for (underlying, asset class) and skipped if one did.
  That read is correct and **not sufficient**: the row it looks for is written
  only *after* the broker accepts, so two signals arriving inside that round
  trip both read zero rows, both pass, and both submit — one position, two
  economic orders.
- A **position claim** is now taken before submission. `INSERT ... ON CONFLICT
  DO NOTHING` against a composite primary key lets exactly one concurrent
  entry proceed; the loser skips with `entry_in_flight`.
- The trade read now runs **again after the claim is won**. The first read and
  the claim are separate round trips, so a caller could read "no trade",
  stall, and win a claim another caller had just released by recording its
  position. Winning the claim is what makes the second read conclusive.
- A claim has a **state, not just a lease**. Only `pending` claims — nothing
  sent to the broker — are reclaimable by time. Once intent is recorded the
  lease stops applying, because a timer expiring is not evidence that no order
  exists. A submission that times out leaves the claim `unknown`, which blocks
  re-entry until reconciled.
- **Intent is recorded before the broker call**, so a process killed mid-submit
  cannot leave an order placed and nothing persisted. On the options path the
  claim's key rides along as `client_order_id`, which Alpaca honours, making a
  retry safe there; `place_equity_order` accepts no such key, so on the equity
  path the broker has no dedup of its own — which is why a submitted claim
  must never free itself on a timer.
- `reconcile_unresolved()` asks the broker what became of each unresolved
  claim and distinguishes **three** answers, where it previously had two. The
  lookup returns a `BrokerVerdict`: `ABSENT` (the broker states positively
  that no such order exists), `PRESENT`, or `INDETERMINATE` — not visible yet,
  degraded, unrecognised. Only `ABSENT` releases. The old
  `Optional[order]` contract collapsed "there is nothing there" and "I cannot
  see one yet" into `None`, and order-by-client-id lookups are not immediately
  consistent, so an order accepted moments earlier could read as absent and
  free the claim guarding it.
- Even `ABSENT` is not believed inside a **settle window** (120s by default,
  measured database-side). A broker that has accepted an order can still answer
  "no such order" briefly; waiting costs one blocked entry, believing it costs
  a duplicate position.
- A lookup that raises still resolves nothing, and each outcome is counted
  separately — `released`, `still_unresolved`, `indeterminate`, `unreachable`,
  `too_fresh` — so a quiet broker cannot look like a clean sweep.
- **Reconciliation now runs.** It had no caller: the mechanism existed and
  nothing ever invoked it, so in production a crashed submit blocked its
  position until a human noticed. A sweep runs at startup — the case the
  lifecycle exists for, since the previous process may have died mid-submit —
  and every 5 minutes from the scheduler, with bounded retries (3, exponential
  backoff) that re-ask only while the broker is the thing not answering. A
  sweep that exhausts them logs CRITICAL naming each blocked position and the
  endpoint that clears it.
- **Broker lookup is per-broker and fails safe by default.**
  `BrokerInterface.find_order_by_client_order_id` returns UNDETERMINED unless
  a broker overrides it, so a broker is opted *in* to authoritative absence
  rather than opted out by omission. Alpaca overrides it against
  `/v2/orders:by_client_order_id`, which is indexed by client id — a 404 there
  is a statement about that id, not an empty page of a filtered list. A 5xx,
  429, timeout, transport error or unexpected status is UNDETERMINED. IBKR
  does not override it, so IBKR claims are never released automatically.
- **An equity claim is never resolved by lookup.** Only the options path sends
  the claim's key as `client_order_id`, so Alpaca's 404 for an equity key
  means "nothing was ever tagged with this" — true, and silent on whether an
  equity order exists. Releasing on it would free the claim on exactly the
  asset class with no broker-side dedup behind it. A guard test fails if
  `place_equity_order` gains a client id and the rule is not updated.
- **Waiting is never evidence.** The settle window only makes an authoritative
  NOT_FOUND believable; it does not turn silence, an unsupported lookup or an
  untransmitted key into absence.
- `force_release()` gives an operator a way to clear a claim the broker will
  never answer for. The delete and an `execution_events` row of kind
  `claim_override` are written in **one transaction**, so a release cannot land
  without a record of who did it and why; an override that matched no claim
  writes nothing. It is reachable over HTTP at
  `GET /api/admin/position-claims` and
  `POST /api/admin/position-claims/{claim_token}/release`, under `/api/admin/`
  because the session allowlist matches on path alone. Identity comes from
  authentication, never the request body, and a caller with no identity is
  refused rather than recorded as nobody; the reason is required and cannot be
  blank.
- Every state change is keyed on a **unique claim token**, so a worker whose
  claim was reclaimed cannot release or advance whoever holds the position
  now. Lease comparisons are **database-side** throughout; no timestamp
  travels from a worker's clock.
- Claims are **global** (`scope` column, always `'global'`). Positions are not
  yet owned — `trades` has no organization column — so scoping claims per
  tenant while the duplicate read beside them stays global would make the two
  disagree. The column exists so the batch that gives trades an owner can fill
  it in without reshaping a primary key.
- A claim that cannot be taken **fails closed**, matching the duplicate read
  beside it, and now says which of the two failed: `entry_guard_unavailable`
  for the claim, `duplicate_check_error` for the read. They previously shared
  one reason, so a test asserting "blocked" passed whether the guard worked or
  something unrelated broke — an accident counting as proof of the intended
  behaviour. Nothing outside the entry path consults claims, so exits, fill
  polling, reconciliation and the kill switch are unaffected when the table is
  missing — pinned by a test.
- A claim survives a process restart and still blocks, which is the case the
  whole lifecycle exists for; tests drive crash → restart → broker lookup →
  resolution through a genuinely new engine and session factory over the same
  rows, including an order that only becomes visible on the second sweep.
- The test suite no longer cancels background tasks indiscriminately. It stops
  three **named** services through their own shutdown APIs
  (`conftest.BACKGROUND_TASK_OWNERS`), because a blanket `asyncio.all_tasks()`
  sweep would silence the same warnings while hiding the lifecycle defect
  behind them — a service with no shutdown path would look clean in the suite
  and go on leaking in production.
- `deploy/hetzner/update.sh` is now **executed** in tests, not just parsed,
  against a real disposable PostgreSQL with Docker stubbed: ordering, a real
  `alembic upgrade head`, and a failed migration leaving the running service
  untouched.
- Deploy ordering is fixed rather than documented around. `update.sh` used to
  start containers and migrate afterwards, leaving new code serving an old
  schema in between, and the runbook's suggested pre-apply —
  `docker exec olbostrade-backend alembic upgrade head` — ran inside the
  container **still on the old image** and so applied nothing while reporting
  success. The migration now runs in a throwaway container from the image just
  built, before `up -d`, and a failure stops the deploy with the old image
  still serving a schema it matches.

## Unreleased — Autopilot is restored from the record again

Reverses the restart behaviour shipped in "Execution-mode changes state their
durability", which refused to restore Autopilot at all. That did close the
unpersisted-reduction hazard, but it disarmed automation on **every** deploy —
a cure firing on every restart for a fault firing on almost none.

- `rehydrate()` restores the recorded mode as recorded, Autopilot included,
  and logs a warning when it does so.
- What makes the record trustworthy instead: **a reduction that fails to
  persist is now retried** (2s, backing off to 60s, 20 attempts) until it
  lands. The window in which the newest row on disk is more permissive than
  the running mode now ends when the database comes back, rather than when
  somebody notices. A retry is abandoned if the mode moves on, so a superseded
  reduction cannot be re-recorded over a newer decision, and giving up is
  logged CRITICAL.
- **Known residual risk, stated rather than designed around:** if the process
  dies during that window — database down, reduction applied in memory, no
  retry landed — nothing durable records the reduction and startup restores
  the older Autopilot row. Recovering that would require the information the
  failed write is precisely what did not record. The kill switch persists
  separately and rehydrates fail-closed; it is the control that does not
  depend on this path.
- Fixes a bug from the same earlier change: `summary()` called with no
  arguments — which is what `GET /api/trade-desk/execution-mode` does —
  reported `persistence: "confirmed"` even while an unrecorded reduction was
  outstanding, hiding exactly what those fields exist to show. It now derives
  the value from state.

## Unreleased — Journal entries are owned by an organization

**Requires a migration (0039). No new environment variables.**

- `journal_entries` had **no ownership column at all** — not `organization_id`,
  not even `user_id` — and every journal route was a shared query. Listing
  returned everyone's entries; fetching or updating by id worked on any row
  regardless of who asked; the analytics endpoints aggregated across all
  tenants.
- Every journal read and write is now scoped to the caller's organization,
  resolved from the authenticated server context (`owner_scope`). Nothing a
  client sends takes part in deciding what it can see.
- **Cross-organization ids answer 404, not 403**, and with the same detail
  string a nonexistent id gets — a 403 would confirm the row exists.
  Ownership is part of the lookup rather than a check afterwards.
- **Migration 0039 does not guess.** `organization_id` is nullable and stays
  that way: a pre-ownership entry's author is not recoverable from the row.
  The backfill assigns existing entries only when there is **exactly one**
  personal organization (the single-operator case). With zero or several it
  leaves them unattributed and raises a NOTICE with the count, because handing
  one customer's trading journal to another is worse than leaving old rows
  unowned.
- **NULL is a scope, not a wildcard.** Unattributed rows match only the
  single-operator scope used when auth is disabled, so they are invisible to
  every organization-scoped query and fail closed.
- `list_entries` no longer swallows an auth failure into
  `{"entries": [], "error": ...}`. Rendering "you are not allowed to see this"
  as "you have nothing" reads as a safe value and is not one.

Isolation for `trades` is **not** in this change — the model is read by eight
route modules (analytics, portfolio, risk, rotation, strategy, paper_trade,
research, trade_desk) and warrants its own batch.

## Unreleased — Strategy validation requires traceable evidence

- **Hardcoded metrics can no longer validate a strategy.** The Research Lab UI
  posted literal values — `sharpe: 1.0`, `oos_sharpe: 0.9` — into the
  transition route, and the gates, which evaluate whatever dict they are
  handed, cleared them. A strategy could reach a validated stage without
  anything ever having been measured. Those literals are gone.
- **Clients may no longer supply metrics at all.** The route refuses
  `metrics` / `wf_metrics` in a request body and instead takes a
  `backtest_run_id`. The server loads that `BacktestRun`, checks it completed
  and that its strategy matches the experiment, reads the metrics from the
  stored row, and writes a `provenance` block (engine, run id, strategy,
  dataset window, starting capital, parameters including commissions and
  slippage, recorded-at). The `sharpe_ratio` → `sharpe` key mapping moved
  server-side too, so a client can no longer get it wrong — nor quietly right
  by sending its own number.
- Evidence without complete provenance is **unverified** and cannot clear a
  gate. Promotion additionally re-checks the backtest behind the experiment,
  so a demo-seeded experiment cannot become live-eligible on the strength of a
  real paper record alone.
- **Existing history is preserved, not rewritten.** Rows written before
  provenance existed keep their numbers and simply stop clearing gates, with
  an explanation naming what is missing. A rejected transition changes
  nothing.
- **The walk-forward → paper transition is disabled.** Nothing in this build
  computes out-of-sample metrics: `oos_sharpe` appears only in the gate and
  the route that fed it, and the sole producer was that literal object in the
  UI. Rather than keep judging whatever a caller sends, the transition now
  refuses and states what is missing. `evaluate_walkforward_gate` is retained
  and still tested, ready for a real engine.

## Unreleased — Broker-neutral emergency stop, with verified flatness

- **Cancellation now works on every broker.** It sat inside
  `if hasattr(self._broker, "ib")`, so on Alpaca the entire step was skipped —
  no orders cancelled, **no error recorded**, `orders_cancelled` left at 0. A
  caller could not tell "nothing to cancel" from "never tried", and working
  orders stayed live while the positions underneath them were flattened. A
  resting entry filling afterwards re-opens the exposure the stop existed to
  remove.
- New `BrokerInterface.cancel_all_open_orders()` sweeps the whole account and
  returns a `CancelSweep` of `requested` / `cancelled` / `unresolved` /
  `enumeration_error`. The existing per-symbol `cancel_open_orders` cannot
  substitute: it only visits symbols the caller already knows about, which
  during a stop means symbols that still have positions — a working order for
  a symbol with **no** position is invisible to it.
- **Flatness is now verified, not inferred.** `positions_flattened` counts
  every non-rejected result, so an accepted-but-unfilled market order, a
  partial fill and a venue cancellation all increment it. A reconciliation
  step re-reads the broker; `reconciliation.flat` is `True` only when no
  non-zero position and no unresolved order remain, and `None` — unknown, not
  `False` — when the read itself failed.
- **Re-engaging re-verifies instead of reassuring.** It returned
  `already_engaged` and nothing else, which reads as success while unresolved
  orders and residual positions sit untouched. It now reconciles and reports
  the exposure.
- **Options flatten orders now state that they close.** `position_intent` was
  hardcoded to `*_to_open` for every leg, so the kill switch asked the broker
  to open a naked short in the contract it was trying to close. `SpreadLeg`
  gains `intent` (`open` by default, so no existing caller changes behaviour);
  the flatten path sets `close`.

## Unreleased — Execution-mode changes state their durability

- Raising automation (Manual → Copilot → Autopilot) now **records the decision
  before activating it**. Previously `set_mode` mutated the runtime first and
  swallowed the write's failure, so a database outage produced a live Autopilot
  and an ordinary success response — the machine trading automatically on a
  decision nothing durably held. A failed write now leaves the mode unchanged
  and the route answers `503` instead of `200` with the old mode in the body.
- Reducing automation, and engaging an emergency stop, still take effect
  **immediately even during a database outage**. Making a safety reduction wait
  for a database that may be the broken thing would be the same mistake with
  the opposite sign. A reduction that could not be recorded is surfaced as
  `persistence: "unconfirmed"` rather than logged and forgotten.
- `summary()` now states durability explicitly: `confirmed`, `unavailable`
  (requested but **not** in force), `unconfirmed` (in force, not recorded) or
  `stale`, plus `requested_mode` when the two differ. A failed write used to be
  indistinguishable from a successful one.
- **Autopilot is no longer auto-restored on restart.** A reduction out of
  Autopilot that failed to persist leaves the older, more permissive row newest
  on disk, so restoring it would hand automation back silently as a side effect
  of a restart. It now restores as Copilot — every signal is kept, a human is
  still asked — with `restore_note` explaining why. **This is a behaviour
  change: Autopilot must be re-engaged explicitly after a deploy.**

## Unreleased — Copilot approval is a single-use atomic claim

- `_resolve_pending_approval` now claims a pending approval with one
  conditional `UPDATE ... WHERE status='pending' RETURNING`, replacing an
  unlocked `SELECT` followed by an ORM write. Under READ COMMITTED that read
  took no row lock, so two concurrent approvals of one signal could both see
  `pending`, both commit, and both return a payload — **two broker orders for
  one signal**. The rotation path next door already used `SELECT ... FOR
  UPDATE`; this path did not.
- A concurrent approve/reject pair now produces exactly one terminal decision
  instead of submitting and recording a rejection for the same signal.
- Approval and rejection record the authenticated actor (`approved_by_actor` /
  `rejected_by_actor`) alongside the existing role label, and the decision is
  merged into the stored payload. When auth is disabled the field is omitted
  rather than filled with a placeholder.
- Concurrency is now covered against a real PostgreSQL
  (`tests/test_approval_concurrency_pg.py`), and CI gained a `postgres:16`
  service so those tests run instead of skipping. The pre-existing mock-based
  tests passed throughout the window in which this bug was live.

## Unreleased — Instrument console UI (skeuomorphic-lite)

- Terminal chrome: raised bezels on panels/buttons, rack ticker + status bars,
  instrument chips on Trade Desk header, physical-style kill + mode cards
- Typography: IBM Plex Sans + JetBrains Mono (replaces Inter)
- Explicitly not glass/neumorphism/clay — crisp console, high contrast

## Unreleased — Trading Style in Trade Desk V2

- Desk Settings now includes Trading Style (Conservative / Balanced /
  Aggressive / Scalper) — was only on the legacy Trade Desk page
- Mode cards show min confidence + daily hard max from the frequency controller

## Unreleased — Position rotation at max concurrent

- When `POSITION_ROTATION_ON_MAX=true` and max open positions blocks a new
  **equity** entry, close N positions (default 2): highest unrealized P&L,
  then lowest `signal_score` (oldest if scores missing). Skips the incoming
  ticker; options closes deferred. Flag **off** by default.

## Unreleased — Trade Desk V2 default-on

- Product defaults: `trade_desk_v2` + equity/options/copilot/execution/replay/
  mobile flags **on**; experimental desks stay off
- Desk Settings copy: default V2 with localStorage rollback to legacy
- Paper E2E decision updated accordingly

## Unreleased — P1 identity / sizing / scan honesty

- Position identity + OMS duplicate guard key on `(underlying, equity|options)`
  so SPY stock and SPY spreads coexist (`trade_identity.py`, `paper_trade`,
  `_execute_signal` Stage 3)
- Equity sizing allows **0 shares** (skip) instead of forcing `max(1, …)`
- Scan panels: “Queue top for approval” (not Auto-execute); removed dead
  EXECUTE LADDER; options `/signal` requires `asset_type=options` + spread
- Paper E2E updated; **V2 flags stay opt-in** (no default-on)

## Unreleased — Feature flags opt-in (pre-deploy)

- Trade Desk V2 flags default **off** (`trade_desk_v2` and desk/monitor/replay/mobile)
  so deploy keeps legacy desk until operator enables V2 in Desk Settings / localStorage
- Desk Settings copy updated to describe opt-in; rollback still `olbos.flags.trade_desk_v2=0`

## Unreleased — Claude Code stage (integration / paper E2E / deploy prep)

- Docs: `docs/trade-desk-2.0/INTEGRATION_AUDIT.md`, `PAPER_E2E.md`, `DEPLOY_PREP.md`
- Hardening: equity `/signal` now carries `shares` + `trade_plan` (composer + scan)
- Read-only smoke: `scripts/paper_e2e_smoke.sh` (no orders; no production deploy)

## Unreleased — Trade Desk 2.0 Phase F (Replay + a11y/mobile)

- Trade Replay MVP: trade tape + detail (paper/live account label, source, journal snapshot)
- Desk shell: skip link, landmarks; scrollable tabs; mobile bottom tab strip (`mobile_trade_desk`)
- History API exposes `trading_mode`; flags `trade_replay_v2` + `mobile_trade_desk` default on

## Unreleased — Step 8 (money-path portfolio gate)

- `_execute_signal` Stage 2b: max positions, underlying/sector concentration, heat-high
- Module `execution_portfolio_gate.py`; Greeks caps off by default (miscalibrated)
- Rollback: `EXECUTION_PORTFOLIO_GATE=false`; evaluate-* endpoints surface the same checks

## Unreleased — Trade Desk 2.0 Phase E (thin Copilot / Orders / Execution)

- Copilot Queue v2: pending approvals + recent decisions audit (existing approve/reject APIs)
- Orders workspace: lifecycle filters from queue + execution log (honest: no broker ack table yet)
- Execution Monitor: timeline + submitted/blocked/skipped/rejected counts
- Flags `copilot_queue_v2` + `execution_monitor_v2` default on; `_execute_signal` unchanged

## Unreleased — Trade Desk 2.0 Phase D (Options Desk)

- Options Desk: discovery + Chain / Chart / Scanner / Income / Flow / Signals /
  0DTE (read-only) tool tabs; intelligence rail with analyze preview + Greeks
- `POST /api/trade-desk/evaluate-options` advisory gate (no `_execute_signal`);
  bans naked shorts / iron condor; 0DTE Autopilot explicitly off
- OptionsChain accepts controlled `symbol`; `options_desk_v2` default on

## Unreleased — Trade Desk 2.0 Phase C (Equity Desk)


- Equity Desk: discovery rail, compact ChartWorkstation embed, intelligence
  rail (bias/alignment/structure + portfolio heat + setup readiness)
- Order composer: `POST /api/trade-desk/evaluate-equity` (advisory, no
  `_execute_signal`) then submit via existing `/api/trade-desk/signal` queue
- ChartWorkstation accepts optional controlled `symbol` + `compact` mode
- Flag `equity_desk_v2` default on; no money-path gate changes

## Unreleased — Trade Desk 2.0 Phase B (shell)


- Feature flag `trade_desk_v2` (default on; rollback via Desk Settings or
  `olbos.flags.trade_desk_v2=0`)
- Trade Desk shell: header (Paper/Live labeled), tabs, Command Overview
  (read-only queues from pending / execution log / positions / risk)
- Sidebar IA under flag: Options under Trade Desk group; options tools stay
  under Strategies (advanced) until Phase D
- Copilot / Positions reuse legacy TradeDesk tabs; Equity/Options/Orders/Replay
  show honest Phase placeholders
- No `_execute_signal` or money-path changes

## Unreleased — Security: kill-switch reset + trade-desk mutate auth


- Kill-switch reset code moved to server env `KILL_SWITCH_RESET_CODE` (no
  default; reset disabled until configured). Frontend prompts for the code;
  the old hardcoded `OLBOSTRADE_MANUAL_RESET` string is removed from the bundle
  and from `POST /api/trade-desk/kill-switch` reset.
- Trade-desk mutate routes (`execution-mode`, `approve`, `reject`,
  `manual-trade`, `signal`) require `X-Api-Key` when `SECRET_KEY` is set.
  Operator pastes the key once per session (Risk → Operator API Key →
  sessionStorage). Engage kill switch remains FastAPI-open for emergencies
  (nginx Basic Auth still applies in production).

## Unreleased — Options Signal cards on Strategies → Signals


- Strategies → Signals: OPTIONS | EQUITIES toggle beside broker/Greeks strip
- Options cards mirror equity layout: strategy, BUY_SPREAD/SELL_SPREAD
  attribution, POP/confidence, IV rank + Greeks pills, credit/debit · max
  loss · breakeven, contracts/DTE
- Backend: in-memory store + `GET /api/options/signals` and
  `POST /api/options/signals/scan` (SPY/QQQ); background scanner persists
  into the same store

### Fixed
- **`POST /api/options/signals/scan` no longer dispatches to execution.**
  It originally called `_run_options_scan()` the same way the scheduled
  background scanner does, ending in `handle_signal()` — meaning a UI
  action that looks like "refresh the signal feed" could submit a real
  order if Autopilot happened to be the active execution mode, with no
  indication of that on the page. `_run_options_scan()` now takes an
  `execute` flag (default `True`, unchanged for the scheduler); the preview
  endpoint passes `execute=False`, computing and persisting the same
  strategy/strike recommendation but never reaching `handle_signal()`. Only
  the scheduled background scanner executes. Verified live against the test
  container in Autopilot mode. Known limitation: iron_condor still never
  appears (execution or display) — its 4-leg structure isn't wired into the
  current spread data model.

## Unreleased — UI/UX Phase 4: landing trust + attribution display

Frontend-only public-page honesty from `docs/ui-ux-phase1-4/PLAN.md` Phase 4.
No trading, risk, execution, sizing, or authentication logic changed. No backend
`source` field added (deferred pending operator approval).

### Changed
- **Landing Free/Pro:** both CTAs are **Open paper terminal** → `/terminal`.
  Removed personal `mailto:` Pro CTA. Capability lists match what the terminal
  actually exposes today; numeric limits labeled **(planned)** / not enforced.
- **Landing footer:** removed five disabled Privacy/Terms/… stubs; replaced with
  “Disclosures coming soon” + Sign In.
- **Landing divergence copy:** scoped to the equity scan panel (where
  `SignalDivergence` is actually wired), not a global promise.

### Known limitations
- Background scanner `/api/equity/signals` may still omit `source`; UI continues
  to show “unknown” / “Source unavailable” via `SignalAttribution` rather than
  inventing an engine name. Populating `source`/`engine` on the payload remains
  an optional backend follow-up.

## Unreleased — UI/UX Phase 3: speak human

Frontend-only glossary and polish from `docs/ui-ux-phase1-4/PLAN.md` Phase 3.
No trading, risk, execution, sizing, or authentication logic changed.

### Added
- **MetricHint** (`MetricHint.tsx`) — one-line hover glossary for Sharpe, Sortino,
  Calmar, Max DD, POP, EV, Kelly, MAE, MFE, IV Rank, Net Theta, consecutive
  losses, and related labels. Wired into Executive Summary tiles, Trade Desk
  approvals + P&L/position headers, Dashboard stats/guardrails, and Chart IV Rank.
- **Chart legend** on ChartWorkstation (SMA-20, VWAP, volume, price levels).
- **CSS loading skeleton** (`.skeleton-block` / `.skeleton-shimmer`) replacing
  bare `LOADING CHART…` text; respects `prefers-reduced-motion`.
- **`.signal-badge`** bullish/bearish/neutral classes so direction chips are not
  styled with trading-style `mode-badge` classes.

### Changed
- Dashboard **Net Theta / day** → **Net Theta (daily)**; **Consec. Loss**
  displays as **Consecutive losses** via the glossary helper.

## Unreleased — UI/UX Phase 2: first 60 seconds

Frontend-only density and wayfinding from `docs/ui-ux-phase1-4/PLAN.md` Phase 2.
No trading, risk, execution, sizing, or authentication logic changed.

### Added
- **Welcome banner** on Command Center (`WelcomeBanner.tsx`) — three steps
  (Broker → Manual mode → Signals), dismissible via `olbos.welcome.dismissed`.
- **TerminalNavContext** so pages can deep-link through the shell without
  prop-drilling.
- **Core vs Advanced nav** filter (`filterNavForDisplay`) with a sidebar
  **Show advanced** toggle (`olbos.nav.advanced`). Deep-linked advanced pages
  stay visible even when Advanced is collapsed.

### Changed
- **ExecutiveSummary** defaults to KPI cards + Portfolio Heat + a short
  Performance strip (Win Rate, Profit Factor, Max DD, Current DD). Strategy
  Health, Meta-Strategy, Capital Allocation, Stress & VaR, System Health, and
  extra ratios sit behind **Show advanced analytics**
  (`olbos.execSummary.advanced`).
- Sidebar Core defaults: Command Center, Markets (Chart + Watchlists), Trade
  Desk, Strategies → Signals, Portfolio & Risk, System → Broker. Options Desk,
  Research, Journal, Performance, and other leaves move under Advanced.

## Unreleased — UI/UX Phase 1: trust labels

Frontend-only honesty fixes from `docs/ui-ux-phase1-4/PLAN.md` Phase 1.
No trading, risk, execution, sizing, or authentication logic changed.

### Fixed
- **ChartWorkstation** no longer shows `AUTOPILOT READY` whenever the kill
  switch is off. The top strip reads the real execution mode
  (`MANUAL` / `COPILOT` / `AUTOPILOT`) and only shows `HALTED` when the kill
  switch is active.
- **ChartWorkstation** bare BUY/SELL chips (and the selected-signal cell)
  now use `SignalAttribution` instead of misusing `mode-badge`
  conservative/scalper classes. Cross-ticker `signals[0]` fallback remains
  removed.
- **“TA Trade Plan”** renamed to **Trade plan** when a scanner signal is
  present (with source when the payload provides it) or **Chart levels**
  when levels are support/resistance fallbacks. ENTRY and RESISTANCE no
  longer share the same color.
- **Sidebar Kill Switch** engages the existing `KillSwitchButton` confirm
  modal instead of navigating to the Risk page.
- **Status bar** shows the active workspace label (e.g. `MARKETS · CHART`)
  instead of a hardcoded `TRADE DESK` on every page.
- **Header execution control** is a single Manual / Copilot / Autopilot
  tri-state (same API as Trade Desk), replacing dual COPILOT/AUTOPILOT
  ON/OFF chips that hid the state machine.
- **Nav / tab labels:** Trade Desk “Orders” → **Desk signals**; “Execution
  Logs” (which opened P&L) → **P&L Breakdown**; new **Execution Log** leaf
  opens the approvals/execution-log surface; Trade Desk tab “Risk profile”
  → **Trading style**.

### Added
- Pure helpers + Vitest coverage for execution-mode display, chart
  attribution mapping, and status-bar nav labels
  (`frontend/src/utils/chartWorkstationDisplay.ts`,
  `frontend/src/utils/navLabels.ts`).

## Unreleased — Frontend trust/UX revision + public landing page

Improved presentation and visibility of existing trading, signal, and risk states.
No trading, risk, execution, or authentication logic was added or changed —
this is a frontend-only change set (see the implementation report delivered
alongside this change for the full evidence table).

### Added
- **Signal attribution** (`frontend/src/components/SignalAttribution.tsx`,
  `frontend/src/types/signal.ts`): a shared component that replaces bare
  BUY/SELL/HOLD badges with direction + source + timeframe + confidence +
  freshness + execution authority, using explicit "unknown"/"unavailable"
  wording for anything the frontend can't verify instead of omitting it.
  Wired into `EquitySignals.tsx`, `OptionsScanPanel.tsx`,
  `EquityScanPanel.tsx`, and the Copilot approvals queue in `TradeDesk.tsx`.
- **Signal divergence disclosure** (`frontend/src/components/SignalDivergence.tsx`):
  a reusable component that surfaces disagreement between two independently
  sourced signals for the same symbol (direction conflict, mixed
  confirmation, or timeframe disagreement) instead of resolving it visually.
  Never claims which signal is execution-authoritative unless that is
  already known from the data.
- **Persistent capital-at-risk status bar**
  (`frontend/src/components/GlobalRiskStatus.tsx`), mounted in
  `TerminalLayout.tsx` above the main content on every authenticated page.
  Shows live/paper environment, AUTOPILOT state, kill-switch state, daily
  drawdown, and remaining daily risk budget (derived only when both the
  current loss and the loss limit are available from the same verified
  source, in the same unit). Every field independently renders
  loading/available/unavailable/unknown — a failed or missing read is never
  displayed as a value that looks safe.
- **Public landing page** (`frontend/src/pages/Landing.tsx`,
  `frontend/src/landing.css`), mounted at `/` via `react-router-dom` (an
  existing, previously-unused dependency). The terminal is unchanged and now
  lives at `/terminal/*`; an unmatched route redirects to `/`. All copy is
  grounded in repository evidence (README, TRADING_POLICY.md,
  ARCHITECTURE_MEMO.md); every performance figure is explicitly labeled as
  unpublished/placeholder, and no guaranteed-return language is used.
- Route-level code splitting (`React.lazy`) for the landing page and the
  terminal bundle, so visiting either surface no longer downloads the other.
- Reduced-motion support: the ticker marquee and status-dot pulse animation
  now respect `prefers-reduced-motion`.
- Minimal test tooling (Vitest + Testing Library — new devDependencies, none
  existed before) and unit tests for `SignalAttribution`, `SignalDivergence`,
  `GlobalRiskStatus`, and `Landing`.

### Changed
- `TerminalLayout.tsx`: mounts `GlobalRiskStatus` between the ticker strip
  and the main content area; marquee marked for reduced-motion.
- `EquitySignals.tsx`, `OptionsScanPanel.tsx`, `EquityScanPanel.tsx`,
  `TradeDesk.tsx`: bare directional badges replaced with
  `SignalAttribution`.
- `index.tsx`: now renders a `BrowserRouter` with lazy-loaded `/` and
  `/terminal/*` routes instead of mounting `App` directly.

### Fixed
- **Terminal-wide crash on bad market data.** Found by running the frontend
  against a real, locally-provisioned backend instead of only a stopped one:
  `/api/market/snapshot/{symbol}` and `/api/market/regime` both omit their
  normal fields on error (a documented yfinance-failure condition, see
  `AUDIT_2026-06.md`), and the ticker strip called `.toFixed()` /
  `.includes()` / `.replace()` on the resulting `undefined` with no error
  boundary above it — blanking the *entire* terminal, not just the ticker.
  Fixed the two unsafe field checks and wrapped the ticker strip, risk-status
  bar, sidebar, and page content each in the existing `ErrorBoundary`
  component so one panel failing can no longer take the others down.
  Regression tests added.

### Known limitations
- This repository has no authentication system (confirmed by direct
  inspection — no router, no session/token handling, a hardcoded account
  name in the sidebar). The `/` vs `/terminal` split is a navigational
  separation only; it does not gate access. Production currently gates the
  *entire* app (including any new public route) behind HTTP Basic Auth at
  the nginx layer when `DASH_USER`/`DASH_PASS` are set
  (`frontend/docker-entrypoint.sh`) — making the landing page genuinely
  public in that deployment requires an infra change (exempting `/` from
  `auth_basic`) that is out of scope here.
- No live example of two reachable, simultaneously-visible panels showing
  opposing signals for the same symbol was found in the current codebase to
  wire `SignalDivergence` into directly; it ships as a tested, ready-to-use
  component for the first place that need arises.
- New devDependencies (`vitest`, `@testing-library/*`, `jsdom`) pull in
  `esbuild <=0.24.2`, flagged by `npm audit` for a dev-server-only
  vulnerability (GHSA-67mh-4wv8-2f99) that does not affect production
  builds or runtime code.

### Proposed — customer broker sign-in prerequisite

- Add a broker picker and Alpaca paper/read-only OAuth login to My Brokers;
  keep IBKR/Schwab visibly unavailable pending integration and approval.
- Encrypt tokens in separate account/organization-scoped storage, consume
  expiring session-bound state atomically, and redact callback request queries.
- Connecting does not authorize or route automated trading. See
  docs/BROKER_OAUTH_ONBOARDING.md for deployment prerequisites and R2 gates.

### Proposed — read-only broker connection health

- Add a tenant/account-bound Alpaca paper account check and visible health,
  restriction and reconnect states. Persist the observation time; keep trading off.
- Reject stale verification writes after disconnect, reauthorization or a newer
  check. Clear token material on account mismatch or rejected broker access.
- Migration 0044 adds health metadata only. Positions/orders/history reconciliation,
  execution authorization and actual paper-broker validation remain outstanding.

### Proposed — selected paper account data

- Add ephemeral account-bound paper reads for balances, positions and nested
  open-order results, with explicit unavailable/incomplete/stale states.
- Validate identity before and after retrieval; discard results if account,
  credentials or local connection state changes. Keep execution disabled.
- Stacked on unmerged PR #161; no default-branch or deployment change. See
  docs/BROKER_PAPER_ACCOUNT_DATA.md for limitations and remaining R2 gates.
